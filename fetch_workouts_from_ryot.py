import os
import requests
import json
import re
import argparse
from dotenv import load_dotenv

from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.write_api import SYNCHRONOUS

load_dotenv()

# --- Configuration ---
GRAPHQL_API_URL = os.getenv("GRAPHQL_API_URL")
AUTH_TOKEN = os.getenv("AUTH_TOKEN")

INFLUXDB_URL = os.getenv("INFLUXDB_URL")
INFLUXDB_TOKEN = os.getenv("INFLUXDB_TOKEN")
INFLUXDB_ORG = os.getenv("INFLUXDB_ORG")
INFLUXDB_BUCKET = os.getenv("INFLUXDB_BUCKET")
DRY_RUN = os.getenv("DRY_RUN", "false").lower() == "true"

def fetch_graphql_data(query, variables=None):
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
            print("--- GraphQL API Error ---")
            print(json.dumps(json_response, indent=2))
            print("-------------------------")
        return json_response
    except requests.exceptions.RequestException as e:
        print(f"Error fetching GraphQL data: {e}")
        return None

def get_workout_ids():
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

def get_workout_details(workout_id):
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

def get_exercise_details(exercise_id):
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

def parse_exercise_id(exercise_id):
    # Extract clean exercise name
    name = exercise_id.split("_reps_and_weight_usr_")[0]
    return slugify(name)

def slugify(text):
    # This regex is corrected to avoid the bad character range error
    return re.sub(r'[^a-zA-Z0-9_\s-]', '', text).strip().lower().replace(" ", "_")

def clear_influxdb_measurements(client):
    """Deletes all data from the workouts and workout_summary measurements."""
    delete_api = client.delete_api()
    start = "1970-01-01T00:00:00Z"
    stop = "2100-01-01T00:00:00Z"
    print("Deleting all existing data from 'workouts' measurement...")
    delete_api.delete(start, stop, '_measurement="workouts"', bucket=INFLUXDB_BUCKET, org=INFLUXDB_ORG)
    print("Deleting all existing data from 'workout_summary' measurement...")
    delete_api.delete(start, stop, '_measurement="workout_summary"', bucket=INFLUXDB_BUCKET, org=INFLUXDB_ORG)
    print("All existing workout data deleted.")

def get_existing_workout_ids(client):
    """Fetches all existing workout IDs from the InfluxDB bucket."""
    query_api = client.query_api()
    query = f'from(bucket: "{INFLUXDB_BUCKET}") |> range(start: 0) |> filter(fn: (r) => r._measurement == "workout_summary") |> keyValues(keyColumns: ["workout_id"]) |> group()'
    try:
        tables = query_api.query(query, org=INFLUXDB_ORG)
        existing_ids = {row.values.get("workout_id") for table in tables for row in table.records}
        return existing_ids
    except Exception as e:
        print(f"Error querying InfluxDB for existing workout IDs: {e}")
        return set()

def main():
    parser = argparse.ArgumentParser(description="Fetch workout data from Ryot and store it in InfluxDB.")
    parser.add_argument("--reset", action="store_true", help="Clear all existing workout data from InfluxDB before importing.")
    args = parser.parse_args()

    print("Starting workout data fetching...")
    source_workout_ids = get_workout_ids()
    if not source_workout_ids:
        print("No workout IDs found from source or error fetching them.")
        return

    print(f"Found {len(source_workout_ids)} workout IDs from source.")

    if DRY_RUN:
        print("\nDRY RUN: No data will be written to InfluxDB.")
        # In dry run, we can't check existing IDs, so we just show a sample.
        details = get_workout_details(source_workout_ids[0])
        if details:
            print(json.dumps(details, indent=2))
        return

    print("\n--- Processing and Writing to InfluxDB ---")
    try:
        with InfluxDBClient(url=INFLUXDB_URL, token=INFLUXDB_TOKEN, org=INFLUXDB_ORG) as client:
            workouts_to_process = []
            if args.reset:
                print("--reset flag detected. Clearing all existing data.")
                clear_influxdb_measurements(client)
                workouts_to_process = source_workout_ids
                print(f"Starting fresh import of {len(workouts_to_process)} workouts.")
            else:
                existing_workout_ids = get_existing_workout_ids(client)
                print(f"Found {len(existing_existing_workout_ids)} existing workout IDs in InfluxDB.")
                workouts_to_process = [id for id in source_workout_ids if id not in existing_workout_ids]
                if not workouts_to_process:
                    print("No new workouts to import.")
                    return
                print(f"Found {len(workouts_to_process)} new workouts to import. Fetching details...")

            write_api = client.write_api(write_options=SYNCHRONOUS)

            for workout_id in workouts_to_process:
                details = get_workout_details(workout_id)
                if not details:
                    print(f"Could not fetch details for workout ID: {workout_id}")
                    continue

                print(f"Processing: {details['details']['name']} ({workout_id})")
                workout_details = details["details"]
                workout_name = workout_details["name"]
                duration = float(workout_details["duration"])
                start_time = workout_details["startTime"]
                end_time = workout_details["endTime"]

                # Write workout summary
                summary_point = Point("workout_summary")
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

                                point = Point("workouts")
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

            print(f"✅ {len(workouts_to_process)} workout(s) successfully written to InfluxDB.")

    except Exception as e:
        print(f"❌ Error during InfluxDB operation: {e}")

    print("✅ Workout processing complete.")


if __name__ == "__main__":
    main()
