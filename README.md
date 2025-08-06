# Ryot to InfluxDB Exporter

This project provides a Python script to fetch workout data from the Ryot Fitness GraphQL API and store it in an InfluxDB instance for analysis and visualization with Grafana.

## How It Works

The script, `fetch_workouts_from_ryot.py`, automates the process of transferring your workout history.

### Data Schema

The script creates two measurements in InfluxDB:

1.  **`workout_summary`**: A summary for each workout.
    *   **Timestamp (`_time`)**: Set to the workout's `startTime`.
    *   **Tags**: `workout_id`, `workout_name`.
    *   **Fields**: `duration` (in seconds).

2.  **`workouts`**: Detailed records for each set within an exercise.
    *   **Timestamp (`_time`)**: Set to the workout's `endTime`.
        *   **Tags**: `workout_id`, `workout_name`, `exercise_name`, `muscle_<muscle_name>` (e.g., `muscle_chest`, `muscle_quads`).
    *   **Fields**: `reps`, `weight`, `volume` (reps * weight), `set_number`, `workout_duration`.

### Execution Flow

By default, the script runs in an incremental update mode:
1.  It fetches all workout IDs from the Ryot API.
2.  It queries your InfluxDB to find which workout IDs have already been imported.
3.  It only downloads the details for new, previously un-imported workouts.

This prevents duplicate data and keeps your database up-to-date efficiently.

## Setup

1.  **Install Dependencies**:
    ```bash
    pip install -r requirements.txt
    ```

2.  **Set Environment Variables**:
    The script requires the following environment variables. You can set them directly in your shell or use a `.env` file.
    *   `GRAPHQL_API_URL`: The URL for the Ryot GraphQL API.
    *   `AUTH_TOKEN`: Your Ryot authentication token.
    *   `INFLUXDB_URL`: The URL of your InfluxDB instance.
    *   `INFLUXDB_TOKEN`: Your InfluxDB authentication token.
    *   `INFLUXDB_ORG`: Your InfluxDB organization.
    *   `INFLUXDB_BUCKET`: The target bucket in InfluxDB.

## Usage

### Standard Run

To fetch only new workouts, run the script without any flags:
```bash
python fetch_workouts_from_ryot.py
```

### Full Reset and Re-import

If you need to fix historical data or start fresh, use the `--reset` flag. This will **delete all existing workout data** from the specified InfluxDB bucket before performing a full re-import of all workouts.

```bash
python fetch_workouts_from_ryot.py --reset
```

### Dry Run

To test the script's connection and see a sample of the data it would fetch without writing anything to InfluxDB, set the `DRY_RUN` environment variable:

```bash
DRY_RUN=true python fetch_workouts_from_ryot.py
```

## Docker & Unraid Deployment

You can also run this script inside a Docker container, which is especially useful for scheduled executions on a server like Unraid.

### Building the Docker Image

To build the image, run the following command in the project's root directory:

```bash
docker build -t ryot-export .
```

### Running with Docker Locally

To run the container locally, you can pass your environment variables using the `--env-file` flag:

```bash
docker run --rm --env-file .env ryot-export
```

The `--rm` flag automatically removes the container when it exits.

### Unraid Setup

1.  **Add Container**: Go to the Docker tab in Unraid and click "Add Container".
2.  **Repository**: Enter the name you used when building the image, e.g., `ryot-export`.
3.  **Environment Variables**: You need to provide the necessary environment variables. The recommended way is to mount your `.env` file.
    *   Click "+ Add another Path, Port, Variable, Label or Device".
    *   **Config Type**: `File`
    *   **Name**: `Env File`
    *   **Container Path**: `/app/.env`
    *   **Host Path**: Enter the path to your `.env` file on your Unraid server (e.g., `/mnt/user/appdata/ryot-export/.env`).
4.  **Post Arguments**: To run the script with the `--reset` flag, add it to the "Post Arguments" field.
5.  **Update Schedule**: To run the script on a schedule, set the "Restart policy" to `No` and add a "Custom" cron job in the "Advanced" section (e.g., `0 3 * * *` to run daily at 3 AM).

By mounting the `.env` file, you can easily manage your credentials without hardcoding them into the Docker template.

### Publishing to Docker Hub (Optional)

If you want to host your image on Docker Hub to easily pull it from any machine, follow these steps:

1.  **Log in to Docker Hub**:
    ```bash
    docker login
    ```

2.  **Tag the Image**: Before you can push the image, you need to tag it with your Docker Hub username.
    ```bash
    # Replace <your-username> with your Docker Hub username
    docker build -t <your-username>/ryot-export:latest .
    ```

3.  **Push the Image**:
    ```bash
    # Replace <your-username> with your Docker Hub username
    docker push <your-username>/ryot-export:latest
    ```

Once pushed, you can use `<your-username>/ryot-export:latest` as the repository when adding the container in Unraid or any other Docker environment.