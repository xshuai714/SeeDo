import copy
import numpy as np
# from src.env import PickPlaceEnv
# from src.env import ALL_BLOCKS, ALL_BOWLS
# from src.LMP import LMP, LMP_wrapper, LMPFGen
# from src.configs import cfg_tabletop, lmp_tabletop_coords
import cv2
import shapely
from shapely.geometry import *
from shapely.affinity import *
import matplotlib.pyplot as plt
# from moviepy.editor import ImageSequenceClip
# import moviepy
from openai import OpenAI
from src.key import mykey
import sys
from IPython.display import display, Image
import base64
from io import BytesIO
import os
import re
from PIL import Image
from collections import Counter
import argparse
import csv
import ffmpy
import ast

from src.vlm_video import extract_frame_list  # import extract_frames

# set up your openai api key
client = OpenAI(api_key=mykey)

# def setup_LMP(env, cfg_tabletop, openai_client):
#     # LMP env wrapper
#     cfg_tabletop = copy.deepcopy(cfg_tabletop)
#     cfg_tabletop["env"] = dict()
#     cfg_tabletop["env"]["init_objs"] = list(env.obj_name_to_id.keys())
#     cfg_tabletop["env"]["coords"] = lmp_tabletop_coords
#     LMP_env = LMP_wrapper(env, cfg_tabletop)
#     # creating APIs that the LMPs can interact with
#     fixed_vars = {"np": np}
#     fixed_vars.update(
#         {
#             name: eval(name)
#             for name in shapely.geometry.__all__ + shapely.affinity.__all__
#         }
#     )
#     variable_vars = {
#         k: getattr(LMP_env, k)
#         for k in [
#             "get_bbox",
#             "get_obj_pos",
#             "get_color",
#             "is_obj_visible",
#             "denormalize_xy",
#             "put_first_on_second",
#             "get_obj_names",
#             "get_corner_name",
#             "get_side_name",
#         ]
#     }
#     variable_vars["say"] = lambda msg: print(f"robot says: {msg}")

#     # creating the function-generating LMP
#     lmp_fgen = LMPFGen(openai_client, cfg_tabletop["lmps"]["fgen"], fixed_vars, variable_vars)

#     # creating other low-level LMPs
#     variable_vars.update(
#         {
#             k: LMP(openai_client, k, cfg_tabletop["lmps"][k], lmp_fgen, fixed_vars, variable_vars)
#             for k in [
#                 "parse_obj_name",
#                 "parse_position",
#                 "parse_question",
#                 "transform_shape_pts",
#             ]
#         }
#     )

#     # creating the LMP that deals w/ high-level language commands
#     lmp_tabletop_ui = LMP(
#         openai_client,
#         "tabletop_ui",
#         cfg_tabletop["lmps"]["tabletop_ui"],
#         lmp_fgen,
#         fixed_vars,
#         variable_vars,
#     )

#     return lmp_tabletop_ui

# def for calling openai api with different prompts
def call_openai_api(prompt_messages):
    params = {
        "model": "gpt-4o",
        "messages": prompt_messages,
        "max_tokens": 400,
        "temperature": 0
    }
    result = client.chat.completions.create(**params)
    return result.choices[0].message.content

def get_object_list(selected_frames):
    # first prompt to get objects in the environment
    prompt_messages_state = [
        {
            "role": "system",
            "content": [
                "You are a visual object detector. Your task is to count and identify the objects in the provided image that are on the desk. Focus on objects classified as grasped_objects and containers. The objects have been outlined with contours of different colors for easier distinction."
            ],
        },
        {
            "role": "user",
            "content": [
                "There are two kinds of objects, grasped_objects and containers in the environment. We only care about objects on the desk. The objects have been outlined with contours of different colors for easier distinction.",
                "Notice that there might be similar objects. You are supposed to use the color of its contour to distinguish between similar objects."
                "Based on the input picture, answer:",
                "1. How many objects are there in the environment?",
                "2. What are these objects?",
                "You should respond in the format of the following example:",
                "Number: 1",
                "Objects: red pepper, red tomato, white bowl (yellow contour), white bowl (red contour)",
                *map(lambda x: {"image": x, "resize": 768}, selected_frames[0:1]),  # use first picture for environment objects
            ],
        },
    ]
    response_state = call_openai_api(prompt_messages_state)
    return response_state

def extract_num_object(response_state):
    # extract number of objects
    num_match = re.search(r"Number: (\d+)", response_state)
    num = int(num_match.group(1)) if num_match else 0
    
    # extract objects
    objects_match = re.search(r"Objects: (.+)", response_state)
    objects_list = objects_match.group(1).split(", ") if objects_match else []
    
    # construct object list
    objects = [obj for obj in objects_list]
    
    return num, objects

def extract_keywords_pick(response):
    try:
        return response.split(': ')[1]
    except IndexError:
        print("Error extracting pick keyword from response:", response)
        return None

def extract_keywords_drop(response):
    try:
        return response.split(': ')[1]
    except IndexError:
        print("Error extracting drop keyword from response:", response)
        return None

def extract_keywords_reference(response):
    try:
        return response.split(': ')[1]
    except IndexError:
        print("Error extracting reference object from response:", response)
        return None

def is_frame_relevant(response):
    return "hand is holding an object" in response.lower()

def parse_closest_object_and_relationship(response):
    pattern = r"Closest Object: ([^,]+), (.+)"
    match = re.search(pattern, response)
    if match:
        return match.group(1), match.group(2)
    print("Error parsing closest object and relationship from response:", response)
    return None, None

def process_images(selected_frames, obj_list):
    string_cache = ""  # cache for CaP operations
    i = 1

    while i < len(selected_frames):
        # Check if the first frame (i) is relevant (i.e., hand is holding an object)
        input_frame_pick = selected_frames[i:i+1]
        prompt_messages_relevance_pick = [
            {
                "role": "system",
                "content": [
                    "You are an operations inspector. You need to check whether the hand in operation is holding an object. The objects have been outlined with contours of different colors for easier distinction."
                ],
            },
            {
                "role": "user",
                "content": [
                    "This is a picture from a pick-and-drop task. Please determine if the hand is holding an object.", 
                    "Respond with 'Hand is holding an object' or 'Hand is not holding an object'.",
                    *map(lambda x: {"image": x, "resize": 768}, input_frame_pick),
                ],
            },
        ]
        response_relevance_pick = call_openai_api(prompt_messages_relevance_pick)
        print(response_relevance_pick)
        if not is_frame_relevant(response_relevance_pick):
            i += 1
            continue

        # which to pick
        prompt_messages_pick = [
            {
                "role": "system",
                "content": [
                    "You are an operation inspector. You need to check which object is being picked in a pick-and-drop task. The objects have been outlined with contours of different colors for easier distinction."
                ],
            },
            {
                "role": "user",
                "content": [
                    f"This is a picture describing the pick state of a pick-and-drop task. The objects in the environment are {obj_list}. One of the objects is being picked by a human hand or robot gripper now. The objects have been outlined with contours of different colors for easier distinction.",
                    "Based on the input picture and object list, answer:",
                    "1. Which object is being picked",
                    "You should respond in the format of the following example:",
                    "Object Picked: red block",
                    *map(lambda x: {"image": x, "resize": 768}, input_frame_pick),
                ],
            },
        ]
        response_pick = call_openai_api(prompt_messages_pick)
        print(response_pick)
        object_picked = extract_keywords_pick(response_pick)

        i += 1

        # Ensure there is another frame for drop and relative position reasoning
        if i >= len(selected_frames):
            break

        # Check if the second frame (i) is relevant (i.e., hand is holding an object)
        input_frame_drop = selected_frames[i:i+1]
        prompt_messages_relevance_drop = [
            {
                "role": "system",
                "content": [
                    "You are an operations inspector. You need to check whether the hand in operation is holding an object. The objects have been outlined with contours of different colors for easier distinction."
                ],
            },
            {
                "role": "user",
                "content": [
                    "This is a picture from a pick-and-drop task. Please determine if the hand is holding an object.",
                    "Respond with 'Hand is holding an object' or 'Hand is not holding an object'.",
                    *map(lambda x: {"image": x, "resize": 768}, input_frame_drop),
                ],
            },
        ]
        response_relevance_drop = call_openai_api(prompt_messages_relevance_drop)
        print(response_relevance_drop)
        if not is_frame_relevant(response_relevance_drop):
            i += 1
            continue

        # closest object 
        prompt_messages_reference = [
            {
                "role": "system",
                "content": [
                    "You are an operation inspector. You need to find the reference object for the placement location of the picked object in the pick-and-place process. Notice that the reference object can vary based on the task. If this is a storage task, the reference object should be the container into which the items are stored. If this is a stacking task, the reference object should be the object that best expresses the orientation of the arrangement. The objects have been outlined with contours of different colors for easier distinction."
                ],
            },
            {
                "role": "user",
                "content": [
                    f"This is a picture describing the drop state of a pick-and-place task. The objects in the environment are {obj_list}. {object_picked} is being dropped by a human hand or robot gripper now.",
                    "Based on the input picture and object list, answer:",
                    f"1. Which object in the rest of object list do you choose as a reference object to {object_picked}",
                    "You should respond in the format of the following example without any additional information or reason steps:",
                    "Reference Object: red block",
                    *map(lambda x: {"image": x, "resize": 768}, input_frame_drop),
                ],
            },
        ]
        response_reference = call_openai_api(prompt_messages_reference)
        print(response_reference)
        object_reference = extract_keywords_reference(response_reference)

        # and relative position
        prompt_messages_relationship = [
            {
                "role": "system",
                "content": [
                    "You are a VLMTutor. You will describe the drop state of a pick-and-drop task from a demo picture. You must pay specific attention to the spatial relationship between picked object and reference object in the picture and be correct and accurate with directions. The objects have been outlined with contours of different colors for easier distinction.",
                    "Pay attention to if the picked object and reference object are in contact. You must specify if they are.",
                ],
                "role": "user",
                "content": [
                    f"This is a picture describing the drop state of a pick-and-drop task. The objects in the environment are object list: {obj_list}. {object_picked} is being dropped by a human hand or robot gripper now. The objects have been outlined with contours of different colors for easier distinction.",
                    f"It is being dropped somewhere near {object_reference}. Based on the input picture and object list, answer:",
                    f"Drop {object_picked} to which relative position to the {object_reference}? You need to mention the name of objects in your answer",
                    f"There are totally six kinds of relative position, and the direction means the visual direction of the picture.",
                    f"1. In (({object_picked} is contained in the {object_reference})"
                    f"2. On top of ({object_picked} is stacked on the {object_reference}, {object_reference} supports {object_picked})",
                    f"3. above ({object_picked} is located in higher position than the {object_reference})",
                    f"4. below ({object_picked} is located in lower position than the {object_reference})",
                    "5. to the left",
                    "6. to the right",
                    f"You must choose one relative position first. For the 'above','below','to the left','to the right' direction, If the {object_picked} and {object_reference} are in contact or overlapped, you must specify '(in contact)' afertwards"
                    "You should respond in the format of the following example without any additional information or reason steps, be sure to mention the object picked and closest object",
                    f"Drop yellow corn to the left of the red chili",
                    f"Drop red chili in the white bowl"
                    f"Drop red tomato to the right of the purple eggplant (in contact)",
                    *map(lambda x: {"image": x, "resize": 768}, input_frame_drop),
                ],
            },
        ]
        response_relationship = call_openai_api(prompt_messages_relationship)
        print(response_relationship)
        string_cache += response_relationship + " and then "
        
        i += 1
        
    return string_cache

def save_results_to_csv(demo_name, num, obj_list, string_cache, output_file):
    """
    将结果保存到指定的 CSV 文件中，追加模式不会覆盖之前的内容。
    如果文件不存在，则创建并写入标题行。
    """
    file_exists = os.path.exists(output_file)

    with open(output_file, mode='a', newline='') as file:
        writer = csv.writer(file)

        # 如果文件不存在，则写入标题行
        if not file_exists:
            writer.writerow(["object", "action list"])
        
        # 写入数据
        writer.writerow([f"{num} objects: {', '.join(obj_list)}", string_cache])

    print(f"Results appended to {output_file}")


def convert_video_to_mp4(input_path):
    """
    Converts the input video file to H.264 encoded .mp4 format using ffmpy.
    The output path will be the same as the input path with '_converted' appended before the extension.
    """
    # Get the file name without extension and append '_converted'
    base_name, ext = os.path.splitext(input_path)
    output_path = f"{base_name}_converted.mp4"

    # Run FFmpeg command to convert the video
    ff = ffmpy.FFmpeg(
        inputs={input_path: None},
        outputs={output_path: '-c:v libx264 -crf 23 -preset fast'}
    )
    ff.run()
    print(f"Video converted successfully: {output_path}")
    return output_path


def main(input_video_path, frame_index_list, demo_name):
    # 如果 frame_index_list 是字符串，使用 ast.literal_eval 将其转换为列表
    # 现在转换为整数列表
    frame_index_list = ast.literal_eval(frame_index_list)

    # video path
    video_path = input_video_path
    # list to store key frames
    selected_raw_frames1 = []
    # list to store key frame indexes
    selected_frame_index = frame_index_list

    # Convert the video to H.264 encoded .mp4 format
    converted_video_path = convert_video_to_mp4(video_path)

    # Open the converted video
    cap = cv2.VideoCapture(converted_video_path)

    # Manually calculate total number of frames
    actual_frame_count = 0
    while cap.isOpened():
        ret, frame = cap.read()
        if not ret:
            break
        actual_frame_count += 1

    # Reset the capture to the beginning of the video
    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)

    print(f"Actual frame count: {actual_frame_count}")

    # Iterate through index list and get the frame list
    for index in selected_frame_index:
        if index < actual_frame_count:
            cap.set(cv2.CAP_PROP_POS_FRAMES, index)
            ret, cv2_image = cap.read()
            if ret:
                selected_raw_frames1.append(cv2_image)
            else:
                print(f"Failed to retrieve frame at index {index}")
        else:
            print(f"Frame index {index} is out of range for this video.")

    # Release video capture object
    cap.release()

    # 调用处理函数
    selected_frames1 = extract_frame_list(selected_raw_frames1)

    response_state = get_object_list(selected_frames1)
    num, obj_list = extract_num_object(response_state)
    print("Number of objects:", num)
    print("available objects:", obj_list)
    # obj_list = "green corn, orange carrot, red pepper, white bowl, glass container"
    # process the key frames
    string_cache = process_images(selected_frames1, obj_list)
    if string_cache.endswith(" and then "):
        my_string = string_cache.removesuffix(" and then ")

    results_file = "./sam2_contour_wooden_block_results.csv"
    save_results_to_csv(demo_name, num, obj_list, string_cache, results_file)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Process video and key frame extraction.")
    parser.add_argument('--input', type=str, required=True, help='Input video path')
    parser.add_argument('--list', type=str, required=True, help='List of key frame indexes')
    parser.add_argument('--demo', type=str, required=True, help='demo name')

    args = parser.parse_args()
    # Call the main function with arguments
    main(args.input, args.list, args.demo)