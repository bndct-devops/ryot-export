import os
import re
import json
import argparse
import logging
from typing import Any, Dict, List, Optional, Set

import requests
from dotenv import load_dotenv
from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.write_api import SYNCHRONOUS

# --- Constants ---
WORKOUT_MEASUREMENT = "workouts"
SUMMARY_MEASUREMENT = "workout_summary"
TIME_RANGE_START = "1970-01-01T00:00:00Z"
TIME_RANGE_STOP = "2100-01-01T00:00:00Z"

# --- Logging Setup ---
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")

load_dotenv()

# --- Configuration ---
GRAPHQL_API_URL = os.getenv("GRAPHQL_API_URL")
AUTH_TOKEN = os.getenv("AUTH_TOKEN")
INFLUXDB_URL = os.getenv("INFLUXDB_URL")
INFLUXDB_TOKEN = os.getenv("INFLUXDB_TOKEN")
INFLUXDB_ORG = os.getenv("INFLUXDB_ORG")
INFLUXDB_BUCKET = os.getenv("INFLUXDB_BUCKET")
DRY_RUN = os.getenv("DRY_RUN", "false").lower() == "true"

def fetch_graphql_data(query: str, variables: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """Fetch data from GraphQL API."""
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {AUTH_TOKEN}"
    }
    payload = {"query": query}
    if variables:
        payload["variables"] = variables

    try:
        response = requests.post(GRAPHQL_API_URL, headers=headers, json=payload)
        response.raise_for_status()
        json_response = response.json()
        if "errors" in json_response:
            logging.error("GraphQL API Error: %s", json.dumps(json_response, indent=2))
        return json_response
    except requests.exceptions.RequestException as e:
        logging.error("Error fetching GraphQL data: %s", e)
        return None

def get_workout_ids() -> List[str]:
    """Get all workout IDs from Ryot API."""
    query = """
    query {
      userWorkoutsList(input: {search: {query: ""}}) {
        response {
          items
        }
      }
    }
    """
    data = fetch_graphql_data(query)
    if data and data.get("data") and data["data"].get("userWorkoutsList"):
        return data["data"]["userWorkoutsList"]["response"]["items"]
    return []

def get_workout_details(workout_id: str) -> Optional[Dict[str, Any]]:
    """Get details for a specific workout."""
    query = """
    query ($workoutId: String!) {
      userWorkoutDetails(workoutId: $workoutId) {
        details {
          id
          name
          duration
          startTime
          endTime
          information {
            exercises {
              id
              sets {
                statistic {
                  reps
                  weight
                }
              }
            }
          }
        }
      }
    }
    """
    variables = {"workoutId": workout_id}
    data = fetch_graphql_data(query, variables)
    if data and data.get("data") and data["data"].get("userWorkoutDetails"):
        return data["data"]["userWorkoutDetails"]
    return None

def get_exercise_details(exercise_id: str) -> Optional[Dict[str, Any]]:
    """Get muscle groups for an exercise."""
    query = """
    query ($exerciseId: String!) {
      exerciseDetails(exerciseId: $exerciseId) {
        muscles
      }
    }
    """
    variables = {"exerciseId": exercise_id}
    data = fetch_graphql_data(query, variables)
    if data and data.get("data") and data["data"].get("exerciseDetails"):
        return data["data"]["exerciseDetails"]
    return None

def parse_exercise_id(exercise_id: str) -> str:
    """Extract clean exercise name from ID."""
    name = exercise_id.split("_reps_and_weight_usr_")[0]
    return slugify(name)

def slugify(text: str) -> str:
    """Slugify a string for tag usage."""
    return re.sub(r'[^a-zA-Z0-9_\s-]', '', text).strip().lower().replace(" ", "_")

def clear_influxdb_measurements(client: InfluxDBClient) -> None:
    """Deletes all data from the workouts and workout_summary measurements."""
    delete_api = client.delete_api()
    logging.info("Deleting all existing data from '%s' measurement...", WORKOUT_MEASUREMENT)
    delete_api.delete(TIME_RANGE_START, TIME_RANGE_STOP, f'_measurement="{WORKOUT_MEASUREMENT}"', bucket=INFLUXDB_BUCKET, org=INFLUXDB_ORG)
    logging.info("Deleting all existing data from '%s' measurement...", SUMMARY_MEASUREMENT)
    delete_api.delete(TIME_RANGE_START, TIME_RANGE_STOP, f'_measurement="{SUMMARY_MEASUREMENT}"', bucket=INFLUXDB_BUCKET, org=INFLUXDB_ORG)
    logging.info("All existing workout data deleted.")

def get_existing_workout_ids(client: InfluxDBClient) -> Set[str]:
    """Fetches all existing workout IDs from the InfluxDB bucket."""
    query_api = client.query_api()
    query = f'from(bucket: "{INFLUXDB_BUCKET}") |> range(start: 0) |> filter(fn: (r) => r._measurement == "{SUMMARY_MEASUREMENT}") |> keyValues(keyColumns: ["workout_id"]) |> group()'
    try:
        tables = query_api.query(query, org=INFLUXDB_ORG)
        existing_ids = {row.values.get("workout_id") for table in tables for row in table.records}
        return existing_ids
    except Exception as e:
        logging.error("Error querying InfluxDB for existing workout IDs: %s", e)
        return set()

def main() -> None:
    """Main entry point for the script."""
    parser = argparse.ArgumentParser(description="Fetch workout data from Ryot and store it in InfluxDB.")
    parser.add_argument("--reset", action="store_true", help="Clear all existing workout data from InfluxDB before importing.")
    args = parser.parse_args()

    logging.info("Starting workout data fetching...")
    source_workout_ids = get_workout_ids()
    if not source_workout_ids:
        logging.warning("No workout IDs found from source or error fetching them.")
        return

    logging.info("Found %d workout IDs from source.", len(source_workout_ids))

    if DRY_RUN:
        logging.info("DRY RUN: No data will be written to InfluxDB.")
        details = get_workout_details(source_workout_ids[0])
        if details:
            logging.info("Sample workout details:\n%s", json.dumps(details, indent=2))
        return

    logging.info("--- Processing and Writing to InfluxDB ---")
    try:
        with InfluxDBClient(url=INFLUXDB_URL, token=INFLUXDB_TOKEN, org=INFLUXDB_ORG) as client:
            if args.reset:
                logging.info("--reset flag detected. Clearing all existing data.")
                clear_influxdb_measurements(client)
                workouts_to_process = source_workout_ids
                logging.info("Starting fresh import of %d workouts.", len(workouts_to_process))
            else:
                existing_workout_ids = get_existing_workout_ids(client)
                logging.info("Found %d existing workout IDs in InfluxDB.", len(existing_workout_ids))
                workouts_to_process = [id for id in source_workout_ids if id not in existing_workout_ids]
                if not workouts_to_process:
                    logging.info("No new workouts to import.")
                    return
                logging.info("Found %d new workouts to import. Fetching details...", len(workouts_to_process))

            write_api = client.write_api(write_options=SYNCHRONOUS)

            for workout_id in workouts_to_process:
                details = get_workout_details(workout_id)
                if not details:
                    logging.warning("Could not fetch details for workout ID: %s", workout_id)
                    continue

                workout_details = details["details"]
                workout_name = workout_details["name"]
                duration = float(workout_details["duration"])
                start_time = workout_details["startTime"]
                end_time = workout_details["endTime"]

                # Write workout summary
                summary_point = Point(SUMMARY_MEASUREMENT)
                summary_point.tag("workout_id", workout_id)
                summary_point.tag("workout_name", workout_name)
                summary_point.field("duration", duration)
                summary_point.time(start_time)
                write_api.write(bucket=INFLUXDB_BUCKET, record=summary_point)

                # Write each set
                if "information" in workout_details and "exercises" in workout_details["information"]:
                    for exercise in workout_details["information"]["exercises"]:
                        exercise_id = exercise["id"]
                        exercise_name = parse_exercise_id(exercise_id)
                        muscles = []
                        exercise_details = get_exercise_details(exercise_id)
                        if exercise_details and "muscles" in exercise_details:
                            muscles = exercise_details["muscles"]

                        if "sets" in exercise:
                            for i, s in enumerate(exercise["sets"]):
                                set_number = float(i + 1)
                                reps = float(s["statistic"].get("reps", 0))
                                weight = float(s["statistic"].get("weight", 0))
                                volume = reps * weight

                                point = Point(WORKOUT_MEASUREMENT)
                                point.tag("workout_id", workout_id)
                                point.tag("workout_name", workout_name)
                                point.tag("exercise_name", exercise_name)
                                if muscles:
                                    point.tag("muscles", ",".join(muscles))
                                point.field("reps", reps)
                                point.field("weight", weight)
                                point.field("volume", volume)
                                point.field("set_number", set_number)
                                point.field("workout_duration", duration)
                                point.time(end_time)
                                write_api.write(bucket=INFLUXDB_BUCKET, record=point)

            logging.info("✅ %d workout(s) successfully written to InfluxDB.", len(workouts_to_process))

    except Exception as e:
        logging.error("❌ Error during InfluxDB operation: %s", e)

    logging.info("✅ Workout processing complete.")

if __name__ == "__main__":
    main()
