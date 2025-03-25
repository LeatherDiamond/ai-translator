import os
import time

from datetime import datetime
from translator import (
    file_upload,
    manage_batches,
    create_batch,
    check_batch_status,
    download_batch_results,
    file_to_jsonl,
    jsonl_to_csv_manual,
)


def upload_all_files(jsonl_dir):
    """
    Uploads all JSONL files in the specified directory, respecting the limit of max 3 files in processing.
    """
    file_ids = []
    for jsonl_file in sorted(os.listdir(jsonl_dir)):
        if jsonl_file.endswith(".jsonl"):
            jsonl_file_path = os.path.join(jsonl_dir, jsonl_file)
            file_id = file_upload(jsonl_file_path)
            if file_id:
                file_ids.append(file_id)
    return file_ids


def create_all_batches(file_ids):
    """
    Creates processing batches with a maximum of 2 active jobs at a time.
    """
    pending_files = file_ids[:]
    batch_ids = []

    while pending_files:
        active_batches = len(
            [
                b
                for b in manage_batches()
                if b["status"]
                in ["in_progress", "cancelling", "finalizing", "validating"]
            ]
        )

        if active_batches < 2:
            file_id = pending_files.pop(0)
            batch_id = create_batch(file_id)
            if batch_id:
                batch_ids.append(batch_id)
        else:
            print("You have 2 or more active batches!")

        time.sleep(5)

    return batch_ids


def monitor_and_download_results(batch_ids, output_dir):
    """
    Monitors batches statuses and downloads completed results, saving with unique names.
    Retries failed batches due to token limit issues.
    """
    retry_batches = []

    while batch_ids or retry_batches:
        for batch_id in batch_ids[:]:
            batch_status = check_batch_status(batch_id)
            current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            if batch_status and batch_status["status"] == "completed":
                output_file_id = batch_status.get("output_file_id")
                if output_file_id:
                    output_filename = os.path.join(
                        output_dir, f"output_{batch_id}.jsonl"
                    )
                    download_batch_results(output_file_id, output_filename)
                    print(
                        f"[{current_time}] Batch {batch_id} completed. Results saved to {output_filename}"
                    )
                batch_ids.remove(batch_id)
                continue

            elif batch_status and batch_status["status"] == "failed":
                errors = batch_status.get("errors", {}).get("data", [])
                error_message = (
                    errors[0].get("message", "No error message provided")
                    if errors
                    else "No error message provided"
                )
                print(
                    f"[{current_time}] Batch {batch_id} failed with error: {error_message}"
                )

                if "Enqueued token limit reached" in error_message:
                    input_file_id = batch_status.get("input_file_id")
                    if input_file_id:
                        print(
                            f"[{current_time}] Batch {batch_id} failed due to token limit. Marking for retry."
                        )
                        retry_batches.append(input_file_id)
                else:
                    print(
                        f"[{current_time}] Batch {batch_id} encountered a non-retryable error. Skipping."
                    )

                batch_ids.remove(batch_id)
                continue

            elif batch_status and batch_status["status"] in [
                "in_progress",
                "cancelling",
                "finalizing",
                "validating",
            ]:
                print(
                    f"[{current_time}] Batch {batch_id} is {batch_status['status']}. Waiting to complete."
                )
                time.sleep(5)
                continue

        if not batch_ids and retry_batches:
            active_batches = len(
                [
                    b
                    for b in manage_batches()
                    if b["status"]
                    in ["in_progress", "cancelling", "finalizing", "validating"]
                ]
            )

            if active_batches == 0:
                print(
                    f"[{current_time}] Retrying failed batches with input_file_ids: {retry_batches}"
                )
                for input_file_id in retry_batches[:]:
                    retry_batch_id = create_batch(input_file_id)
                    if retry_batch_id:
                        batch_ids.append(retry_batch_id)
                        retry_batches.remove(input_file_id)
            else:
                print(
                    f"[{current_time}] Waiting for active batches to complete before retrying failed batches."
                )
                time.sleep(10)

    print("[INFO] Monitoring complete. All batches processed.")


def main():
    """
    Main workflow for processing CSV translation with Batch API.
    Workflow steps:
    Step 1: Convert CSV to JSONL format;
    Step 2: Upload JSONL files to OpenAI servers;
    Step 3: Create batches for each file with max 3 simultaneous tasks;
    Step 4: Monitor and download results of completed tasks;
    Step 5: Convert downloaded JSONL files back to CSV;
    """
    csv_file = "input.csv"
    output_dir = "output_jsonl/"

    file_to_jsonl(csv_file, output_dir)

    jsonl_files = upload_all_files(output_dir)

    batch_ids = create_all_batches(jsonl_files)

    monitor_and_download_results(batch_ids, output_dir)

    translated_files = [
        f
        for f in os.listdir(output_dir)
        if f.startswith("output_") and f.endswith(".jsonl")
    ]

    if translated_files:
        output_csv_file = "translated_output.csv"
        jsonl_to_csv_manual(output_dir, output_csv_file)
        print(f"Translation completed successfully. Output saved to {output_csv_file}")
    else:
        print("No translated files found. Exiting without generating a CSV output.")


if __name__ == "__main__":
    main()
