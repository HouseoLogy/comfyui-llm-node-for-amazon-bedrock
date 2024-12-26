import json
import os
import re
import base64
from io import BytesIO
from random import randint
import logging
from datetime import datetime
import requests
from retry import retry
from PIL import Image
import numpy as np
import torch
from .session import get_client
import time
import boto3

MAX_RETRY = 3
def get_default_region():
    """获取默认的 AWS 区域"""
    session = boto3.Session()
    return session.region_name

def get_account_id():
    """获取当前 AWS 账户 ID"""
    sts_client = boto3.client('sts')
    return sts_client.get_caller_identity().get('Account')


def download_video_for_invocation_arn(invocation_arn, bucket_name, destination_folder):
    """
    This function downloads the video file for the given invocation ARN.
    """
    invocation_id = invocation_arn.split("/")[-1]

    # Create the local file path
    file_name = f"{invocation_id}.mp4"
    import os

    output_folder = os.path.abspath(destination_folder)
    local_file_path = os.path.join(output_folder, file_name)

    # Ensure the output folder exists
    os.makedirs(output_folder, exist_ok=True)

    # Create an S3 client
    s3 = boto3.client("s3")

    # List objects in the specified folder
    response = s3.list_objects_v2(Bucket=bucket_name, Prefix=invocation_id)

    # Find the first MP4 file and download it.
    for obj in response.get("Contents", []):
        object_key = obj["Key"]
        if object_key.endswith(".mp4"):
            print(f"""Downloading "{object_key}"...""")
            s3.download_file(bucket_name, object_key, local_file_path)
            print(f"Downloaded to {local_file_path}")
            return local_file_path

    # If we reach this point, no MP4 file was found.
    print(f"Problem: No MP4 file was found in S3 at {bucket_name}/{invocation_id}")

def get_job_id_from_arn(invocation_arn):
    return invocation_arn.split("/")[-1]

def save_completed_job(job, output_folder="output"):
    job_id = get_job_id_from_arn(job["invocationArn"])

    output_folder_abs = os.path.abspath(
        f"{output_folder}/{get_folder_name_for_job(job)}"
    )

    # Ensure the output folder exists
    os.makedirs(output_folder_abs, exist_ok=True)

    status_file = os.path.join(output_folder_abs, "completed.json")

    if is_video_downloaded_for_invocation_job(job, output_folder=output_folder):
        print(f"Skipping completed job {job_id}, video already downloaded.")
        return

    s3_bucket_name = (
        job["outputDataConfig"]["s3OutputDataConfig"]["s3Uri"]
        .split("//")[1]
        .split("/")[0]
    )

    localPath = download_video_for_invocation_arn(
        job["invocationArn"], s3_bucket_name, output_folder_abs
    )
    return localPath


bedrock_runtime_client = get_client(service_name="bedrock-runtime")
region = get_default_region()
account_id = get_account_id()
s3_destination_bucket = f"sagemaker-{region}-{account-id}"

class BedrockNovaVideo:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "prompt": ("STRING", {"multiline": True}),
                "dimension": (
                    [
                        "1024 x 720"
                    ],
                ),
                "seed": (
                    "INT",
                    {
                        "default": 0,
                        "min": 0,
                        "max": 2147483646,
                        "step": 1,
                        "round": 1,  # The value represeting the precision to round to, will be set to the step value by default. Can be set to False to disable rounding.
                        "display": "number",
                    },
                ),
            },
            "optional": {
                "image": ("IMAGE",),
            },
        }

    RETURN_TYPES = ("STRING",)
    FUNCTION = "forward"
    CATEGORY = "aws"

    @retry(tries=MAX_RETRY)
    def forward(self, image, prompt, dimension,seed):
        input_image_base64=None
        if image:
            image = image[0] * 255.0
            image = Image.fromarray(image.clamp(0, 255).numpy().round().astype(np.uint8))
            buffer = BytesIO()
            image.save(buffer, format="PNG")

            image_data = buffer.getvalue()
            input_image_base64 = base64.b64encode(image_data).decode("utf-8")

        model_input = {
            "taskType": "TEXT_VIDEO",
            "textToVideoParams": {
                "text": video_prompt,
                "images": [
                    {
                        "format": "png",  # May be "png" or "jpeg"
                        "source": {
                            "bytes": input_image_base64
                        }
                    }
                ]
                },
            "videoGenerationConfig": {
                "durationSeconds": 6,  # 6 is the only supported value currently.
                "fps": 24,  # 24 is the only supported value currently.
                "dimension": dimension,  # "1280x720" is the only supported value currently.
                "seed": seed # A random seed guarantees we'll get a different result each time this code runs.
            },
        }
        
        
        invocation = bedrock_runtime.start_async_invoke(
        modelId="amazon.olympus-video-generator-v1:0",
        modelInput=model_input,
        outputDataConfig={"s3OutputDataConfig": {"s3Uri": f"s3://{s3_destination_bucket}"}},
    )

        invocation_arn = invocation["invocationArn"]
        print("\nResponse:")
        print(json.dumps(invocation, indent=2, default=str))
        
        save_local_path = ""
    
        # Save the invocation details for later reference. Helpful for debugging and reporting feedback.
        while True:
            job_update = bedrock_runtime.get_async_invoke(invocationArn=invocation_arn)
            status = job_update["status"]

            if status == "Completed":
                save_local_path = save_completed_job(job_update, output_folder="/home/ubuntu/ComfyUI/output/")
                break
            else:
                time.sleep(5)
        return (save_local_path,)





NODE_CLASS_MAPPINGS = {
    "Bedrock - Nova Video": BedrockNovaVideo
}