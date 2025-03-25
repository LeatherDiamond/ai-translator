import os
import re
import json
import requests
import tiktoken

from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv())

api_key = os.getenv("api_key")


def count_tokens(text, model="gpt-4o"):
    """
    Used to count tokens in the original file to split it as needed due to token limits in the AI model.
    """
    try:
        tokenizer = tiktoken.encoding_for_model(model)
    except KeyError:
        tokenizer = tiktoken.get_encoding("cl100k_base")
    return len(tokenizer.encode(text))


def split_text_into_chunks_with_tags(text, max_tokens, model="gpt-4o"):
    """
    Used to split the original file due to request length limits in the AI model.
    """
    parts = re.split(r"(\{\{tag_\d+\}\})", text)

    chunks = []
    current_chunk = ""
    current_chunk_length = 0

    for part in parts:
        if not part:
            continue

        part_length = count_tokens(part, model)

        if current_chunk_length + part_length > max_tokens * 0.9:
            if current_chunk.endswith("{{tag_1}}"):
                current_chunk = current_chunk[: -len("{{tag_1}}")]
                chunks.append(current_chunk.strip())
                current_chunk = "{{tag_1}}" + part
                current_chunk_length = count_tokens("{{tag_1}}" + part, model)
            else:
                chunks.append(current_chunk.strip())
                current_chunk = part
                current_chunk_length = part_length
        else:
            current_chunk += part
            current_chunk_length += part_length

    if current_chunk.strip():
        chunks.append(current_chunk.strip())

    return chunks


def extract_html_tags(text):
    """
    Used in the original file to extract html tags and specia symbols because this type of information does not require translation.
    Additionally, these tags can be quite lengthy, potentially consuming unnecessary tokens.
    """
    tag_dict = {}
    tag_id_counter = 1
    pattern = re.compile(r"<[^>]+>|\n|\"|\b\d+\|\|\|\d+\b|\b\d+\|\|\|\d+\w*")

    def replacement(match):
        nonlocal tag_id_counter
        tag = match.group(0)

        if tag not in tag_dict.values():
            tag_id = f"{{{{tag_{tag_id_counter}}}}}"
            tag_dict[tag_id] = tag
            tag_id_counter += 1
            return tag_id
        else:
            for existing_id, existing_tag in tag_dict.items():
                if existing_tag == tag:
                    return existing_id

    text = re.sub(pattern, replacement, text)
    return text, tag_dict


def file_to_jsonl(
    file_path,
    output_dir,
    max_tokens_per_batch=89900,
    max_tokens_per_request=950,
    max_requests_per_file=500,
):
    """
    Creates a JSONL file from the original CSV file within the specified token limits,
    including a prompt for further simulated interaction with the AI.
    """
    translation_language = input("Enter the language into what you want to translate:")
    os.makedirs(output_dir, exist_ok=True)

    with open(file_path, "r", encoding="utf-8") as file:
        raw_data = file.read()

        raw_data, tag_dict = extract_html_tags(raw_data)

        current_batch_tokens = 0
        current_batch_requests = []
        file_count = 1
        request_count = 0
        global_request_id = 1

        system_message = (
            f"You are a translation assistant. Translate the following text to {translation_language}."
            "The following STEPS must be followed. Whenever you are forming a response, ensure all STEPS have been followed otherwise start over, forming a new response and repeat until the finished response follows all the STEPS. Then send the response."
            "STEPS:"
            "STEP-1: Keep {{tag_x}} tags with numbers as they are."
            "STEP-2: You must not miss the data from user's input in your responses especially {{tag_x}} tags, special symbols '{{{', '}}}', '|||' etc.!"
            "STEP-3: Just translate. No comments or explanations."
            "STEP-4: If you can't assist with the request just return the request as an answer."
        )

        system_tokens = count_tokens(system_message, model="gpt-4o")
        user_tokens = count_tokens(raw_data, model="gpt-4o")

        if user_tokens > max_tokens_per_request:
            text_chunks = split_text_into_chunks_with_tags(
                raw_data, max_tokens_per_request - system_tokens - 100
            )
        else:
            text_chunks = [tiktoken.get_encoding("cl100k_base").encode(raw_data)]

        for chunk in text_chunks:
            tokenizer = tiktoken.get_encoding("cl100k_base")
            chunk_tokens = tokenizer.encode(chunk)
            total_tokens = system_tokens + len(chunk_tokens) + 100

            if (
                current_batch_tokens + total_tokens > max_tokens_per_batch
                or request_count >= max_requests_per_file
            ):
                output_file = os.path.join(
                    output_dir, f"batch_requests_part_{file_count}.jsonl"
                )
                with open(output_file, "w", encoding="utf-8") as jsonlfile:
                    for request in current_batch_requests:
                        jsonlfile.write(json.dumps(request) + "\n")
                print(
                    f"Batch file {output_file} created with {len(current_batch_requests)} requests."
                )

                current_batch_requests = []
                current_batch_tokens = 0
                request_count = 0
                file_count += 1

            batch_request = {
                "custom_id": f"request-{global_request_id}",
                "method": "POST",
                "url": "/v1/chat/completions",
                "body": {
                    "model": "gpt-4o",
                    "messages": [
                        {"role": "system", "content": system_message},
                        {"role": "user", "content": chunk},
                    ],
                    "max_tokens": 1000,
                    "temperature": 0,
                },
            }

            current_batch_requests.append(batch_request)
            current_batch_tokens += total_tokens
            request_count += 1
            global_request_id += 1

    if current_batch_requests:
        output_file = os.path.join(
            output_dir, f"batch_requests_part_{file_count}.jsonl"
        )
        with open(output_file, "w", encoding="utf-8") as jsonlfile:
            for request in current_batch_requests:
                jsonlfile.write(json.dumps(request) + "\n")
        print(
            f"Batch file {output_file} created with {len(current_batch_requests)} requests."
        )

    tag_dict_file = os.path.join(output_dir, "tag_dict.json")
    with open(tag_dict_file, "w", encoding="utf-8") as tag_file:
        json.dump(tag_dict, tag_file)

    print(f"Conversion completed. Files saved in the directory {output_dir}")


def file_upload(jsonl_file):
    """
    Uploads the file to OpenAI servers for subsequent task creation.
    """
    url = "https://api.openai.com/v1/files"
    headers = {"Authorization": f"Bearer {api_key}"}

    with open(jsonl_file, "rb") as file:
        files = {"file": (jsonl_file, file), "purpose": (None, "batch")}
        response = requests.post(url, headers=headers, files=files)

    if response.status_code != 200:
        print(
            f"Error while downloading the file: {response.status_code}, {response.text}"
        )
        return None

    file_id = response.json()["id"]
    print(f"File successfully downloaded, file ID: {file_id}")
    return file_id


def create_batch(file_id):
    """
    Creates a translation task via BatchAPI, with a 24-hour completion window.
    """
    url = "https://api.openai.com/v1/batches"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    data = {
        "input_file_id": file_id,
        "endpoint": "/v1/chat/completions",
        "completion_window": "24h",
        "metadata": {"description": "Translation batch job"},
    }

    response = requests.post(url, headers=headers, json=data)

    if response.status_code != 200:
        print(f"Error while creating a batch: {response.status_code}, {response.text}")
        return None

    batch_id = response.json()["id"]
    print(f"Batch successfully created with ID: {batch_id}")
    return batch_id


def cancel_batch(batch_id):
    """
    Cancels the translation task.
    """
    url = f"https://api.openai.com/v1/batches/{batch_id}/cancel"
    headers = {"Authorization": f"Bearer {api_key}"}

    response = requests.post(url, headers=headers)

    if response.status_code != 200:
        print(
            f"Error while canceling the batch: {response.status_code}, {response.text}"
        )
        return None

    print(f"Batch {batch_id} successfully cancelled.")
    return response.json()


def check_batch_status(batch_id):
    """
    Checks the status of the current task.
    """
    url = f"https://api.openai.com/v1/batches/{batch_id}"
    headers = {"Authorization": f"Bearer {api_key}"}

    response = requests.get(url, headers=headers)

    if response.status_code != 200:
        print(
            f"Error while trying to get job id: {response.status_code}, {response.text}"
        )
        return None

    return response.json()


def check_active_batches():
    """
    Checks active jobs on OpenAI servers.
    """
    url = "https://api.openai.com/v1/batches"
    headers = {"Authorization": f"Bearer {api_key}"}

    response = requests.get(url, headers=headers)

    if response.status_code != 200:
        print(
            f"Error while collecting active jobs: {response.status_code}, {response.text}"
        )
        return None

    return response.json()


def manage_batches():
    """
    Fetches job statuses and returns them as a list of dictionaries.
    """
    active_batches = check_active_batches()

    if active_batches and "data" in active_batches:
        batch_info = [
            {"id": batch["id"], "status": batch["status"]}
            for batch in active_batches["data"]
        ]
        for batch in batch_info:
            print(f"Job ID: {batch['id']}, Status: {batch['status']}")
        return batch_info

    return []


def download_batch_results(job_id, output_jsonl_file):
    """
    Downloads the translated file.
    """
    url = f"https://api.openai.com/v1/files/{job_id}/content"
    headers = {"Authorization": f"Bearer {api_key}"}

    response = requests.get(url, headers=headers)

    if response.status_code != 200:
        print(
            f"Error while downloading the results: {response.status_code}, {response.text}"
        )
        return None

    with open(output_jsonl_file, "wb") as f:
        f.write(response.content)

    print(f"Results saved in {output_jsonl_file}")


def restore_img_tags(text, tag_dict):
    """
    Restores tags from a separate file.
    """
    sorted_tags = sorted(
        tag_dict.items(), key=lambda x: int(re.search(r"\d+", x[0]).group())
    )

    for tag_id, tag in sorted_tags:
        text = text.replace(tag_id, tag)
    return text


def merge_jsonl_files(jsonl_dir):
    """
    Merge all JSONL files from the mentioned directory into one list.
    For every 'custom_id', collect only 'content' from the 'role': 'assistant'.
    """
    all_requests = []

    img_dict_file = os.path.join(jsonl_dir, "tag_dict.json")
    with open(img_dict_file, "r", encoding="utf-8") as img_file:
        img_dict = json.load(img_file)

    for jsonl_file in sorted(os.listdir(jsonl_dir)):
        if jsonl_file.endswith(".jsonl"):
            jsonl_path = os.path.join(jsonl_dir, jsonl_file)

            with open(jsonl_path, "r", encoding="utf-8") as file:
                for line in file:
                    data = json.loads(line)

                    custom_id = data.get("custom_id", None)
                    for choice in (
                        data.get("response", {}).get("body", {}).get("choices", [])
                    ):
                        if choice.get("message", {}).get("role") == "assistant":
                            content = choice["message"]["content"]
                            restored_content = restore_img_tags(content, img_dict)

                            if custom_id is not None:
                                all_requests.append(
                                    {
                                        "custom_id": custom_id,
                                        "content": restored_content,
                                    }
                                )
    return all_requests


def jsonl_to_csv_manual(jsonl_dir, output_csv_file):
    """
    Converts JSONL back to the original CSV format.
    """
    merged_data = merge_jsonl_files(jsonl_dir)

    sorted_data = sorted(
        merged_data, key=lambda x: int(re.search(r"\d+", x["custom_id"]).group())
    )

    with open(output_csv_file, "w", encoding="utf-8") as outfile:
        for row in sorted_data:
            cleaned_content = row["content"]
            cleaned_content = re.sub(r'\\"', "", cleaned_content)
            outfile.write(cleaned_content)

    print(f"CSV file with data from JSONL saved as {output_csv_file}")
