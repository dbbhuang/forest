"""
Module used to impute missing data, by combining functions defined in other
modules and calculate summary statistics of imputed trajectories.
"""

import os
import sys
import pickle
import time
import json
import numpy as np
import pandas as pd
import requests
from shapely.geometry import Point
from shapely.geometry.polygon import Polygon
from ..bonsai.simulate_gps_data import boundingBox
from ..poplar.legacy.common_funcs import (
    stamp2datetime,
    datetime2stamp,
    read_data,
    write_all_summaries,
)
from .data2mobmat import (
    great_circle_dist,
    pairwise_great_circle_dist,
    GPS2MobMat,
    InferMobMat,
)
from .mobmat2traj import num_sig_places, locate_home, ImputeGPS, Imp2traj
from .sogp_gps import BV_select


def gps_summaries(
    traj,
    tz_str,
    option,
    places_of_interest=None,
    save_log=False,
    threshold=None,
    split_day_night=False,
):
    """
    This function derives summary statistics from the imputed trajectories
    if the option is hourly, it returns
    ["year","month","day","hour","obs_duration","pause_time","flight_time","home_time",
    "max_dist_home", "dist_traveled","av_flight_length","sd_flight_length",
    "av_flight_duration","sd_flight_duration"]
    if the option is daily, it additionally returns
    ["obs_day","obs_night","radius","diameter","num_sig_places","entropy"]
    Args: traj: 2d array, output from Imp2traj(), which is a n by 8 mat,
            with headers as [s,x0,y0,t0,x1,y1,t1,obs]
            where s means status (1 as flight and 0 as pause),
            x0,y0,t0: starting lat,lon,timestamp,
            x1,y1,t1: ending lat,lon,timestamp,
            obs (1 as observed and 0 as imputed)
          tz_str: timezone
          option: 'daily' or 'hourly'
          places_of_interest: list of amenities or leisure places to watch,
            keywords as used in openstreetmaps
          save_log, bool, True if you want to output a log of locations
            visited and their tags
          threshold, int, time spent in a pause needs to exceed the threshold
            to be placed in the log
          only if save_log True, in minutes
          split_day_night, bool, True if you want to split all metrics to
            datetime and nighttime patterns
          only for daily option
    Return: a pd dataframe, with each row as an hour/day,
             and each col as a feature/stat
            a dictionary, contains log of tags of all locations visited
             from openstreetmap
    """

    if option == "hourly":
        split_day_night = False

    if places_of_interest is not None or save_log:
        pause_vec = traj[traj[:, 0] == 2]
        lat = []
        lon = []
        for row in pause_vec:
            if len(lat) == 0:
                lat.append(row[1])
                lon.append(row[2])
            elif (
                np.min(great_circle_dist(row[1], row[2], np.array(lat), np.array(lon)))
                > 1000
            ):
                lat.append(row[1])
                lon.append(row[2])

        query = "[out:json];\n("

        for i, _ in enumerate(lat):
            bbox = boundingBox(lat[i], lon[i], 1000)

            query += "\n\tnode" + str(bbox) + "['leisure'];"
            query += "\n\tway" + str(bbox) + "['leisure'];"
            query += "\n\tnode" + str(bbox) + "['amenity'];"
            query += "\n\tway" + str(bbox) + "['amenity'];"

        query += "\n);\nout geom qt;"

        overpass_url = "http://overpass-api.de/api/interpreter"

        tries = 0
        while True:
            response = requests.get(overpass_url, params={"data": query}, timeout=300)
            if response.status_code != 200:
                # quit after third try without response
                if tries == 2:
                    print_msg = "Too many Overpass requests in a short time."
                    print_msg += " Please try again in a minute..."
                    sys.stdout.write(print_msg)
                    sys.exit()
                tries += 1
                time.sleep(60)
            else:
                break

        res = response.json()
        ids = {}
        locations = {}
        tags = {}

        for element in res["elements"]:

            element_id = element["id"]

            if "amenity" in element["tags"]:
                if element["tags"]["amenity"] not in ids.keys():
                    ids[element["tags"]["amenity"]] = [element_id]
                else:
                    ids[element["tags"]["amenity"]].append(element_id)
            elif "leisure" in element["tags"]:
                if element["tags"]["leisure"] not in ids.keys():
                    ids[element["tags"]["leisure"]] = [element_id]
                else:
                    ids[element["tags"]["leisure"]].append(element_id)

            if element["type"] == "node":
                locations[element_id] = [[element["lat"], element["lon"]]]
            elif element["type"] == "way":
                locations[element_id] = [
                    [x["lat"], x["lon"]] for x in element["geometry"]
                ]

            tags[element_id] = element["tags"]

    obs_traj = traj[traj[:, 7] == 1, :]
    home_x, home_y = locate_home(obs_traj, tz_str)
    summary_stats = []
    log_tags = {}
    if option == "hourly":
        # find starting and ending time
        sys.stdout.write("Calculating the hourly summary stats..." + "\n")
        time_list = stamp2datetime(traj[0, 3], tz_str)
        time_list[4] = 0
        time_list[5] = 0
        start_stamp = datetime2stamp(time_list, tz_str) + 3600
        time_list = stamp2datetime(traj[-1, 3], tz_str)
        time_list[4] = 0
        time_list[5] = 0
        end_stamp = datetime2stamp(time_list, tz_str)
        # start_time, end_time are exact points
        # (if it ends at 2019-3-8 11 o'clock, then 11 shouldn't be included)
        window = 60 * 60
        no_windows = (end_stamp - start_stamp) // window

    if option == "daily":
        # find starting and ending time
        sys.stdout.write("Calculating the daily summary stats..." + "\n")
        time_list = stamp2datetime(traj[0, 3], tz_str)
        time_list[3] = 0
        time_list[4] = 0
        time_list[5] = 0
        start_stamp = datetime2stamp(time_list, tz_str)
        time_list = stamp2datetime(traj[-1, 3], tz_str)
        time_list[3] = 0
        time_list[4] = 0
        time_list[5] = 0
        end_stamp = datetime2stamp(time_list, tz_str) + 3600 * 24
        # if it starts from 2019-3-8 11 o'clock,
        # then our daily summary starts from 2019-3-9)
        window = 60 * 60 * 24
        no_windows = (end_stamp - start_stamp) // window
        if split_day_night:
            no_windows *= 2

    if no_windows > 0:
        for i in range(no_windows):
            if split_day_night:
                i2 = i // 2
            else:
                i2 = i
            start_time = start_stamp + i2 * window
            end_time = start_stamp + (i2 + 1) * window
            current_time_list = stamp2datetime(start_time, tz_str)
            year = current_time_list[0]
            month = current_time_list[1]
            day = current_time_list[2]
            hour = current_time_list[3]
            # take a subset, the starting point of the last traj <end_time
            # and the ending point of the first traj >start_time
            index_rows = (traj[:, 3] < end_time) * (traj[:, 6] > start_time)

            if split_day_night:
                current_time_list2 = current_time_list.copy()
                current_time_list3 = current_time_list.copy()
                current_time_list2[3] = 8
                current_time_list3[3] = 20
                t2 = datetime2stamp(current_time_list2, tz_str)
                t3 = datetime2stamp(current_time_list3, tz_str)
                if i % 2 == 0:
                    # datetime
                    index_rows = (traj[:, 3] <= t3) * (traj[:, 6] >= t2)
                else:
                    # nighttime
                    index1 = (traj[:, 6] < t2) * (traj[:, 3] < end_time) * (traj[:, 6] > start_time)
                    index2 = (traj[:, 3] > t3) * (traj[:, 3] < end_time) * (traj[:, 6] > start_time)
                    stop1 = sum(index1) - 1
                    stop2 = sum(index1)
                    index_rows = index1 + index2

            temp = traj[index_rows, :]
            # take a subset which is exactly one hour/day,
            # cut the trajs at two ends proportionally
            if i2 not in (0, no_windows - 1):
                if split_day_night and i % 2 == 0:
                    t0_temp = t2
                    t1_temp = t3
                else:
                    t0_temp = start_time
                    t1_temp = end_time

                if sum(index_rows) == 1:
                    p0 = (t0_temp - temp[0, 3]) / (temp[0, 6] - temp[0, 3])
                    p1 = (t1_temp - temp[0, 3]) / (temp[0, 6] - temp[0, 3])
                    x0 = temp[0, 1]
                    x1 = temp[0, 4]
                    y0 = temp[0, 2]
                    y1 = temp[0, 5]
                    temp[0, 1] = (1 - p0) * x0 + p0 * x1
                    temp[0, 2] = (1 - p0) * y0 + p0 * y1
                    temp[0, 3] = t0_temp
                    temp[0, 4] = (1 - p1) * x0 + p1 * x1
                    temp[0, 5] = (1 - p1) * y0 + p1 * y1
                    temp[0, 6] = t1_temp
                else:
                    if split_day_night and i % 2 != 0:
                        t0_temp = [start_time, t3]
                        t1_temp = [t2, end_time]
                        start_temp = [0, stop2]
                        end_temp = [stop1, -1]
                        for j in range(2):
                            p0 = (temp[start_temp[j], 6] - t0_temp[j]) / (
                                temp[start_temp[j], 6] - temp[start_temp[j], 3]
                            )
                            p1 = (t1_temp[j] - temp[end_temp[j], 3]) / (
                                temp[end_temp[j], 6] - temp[end_temp[j], 3]
                            )
                            temp[start_temp[j], 1] = (1 - p0) * temp[
                                start_temp[j], 4
                            ] + p0 * temp[start_temp[j], 1]
                            temp[start_temp[j], 2] = (1 - p0) * temp[
                                start_temp[j], 5
                            ] + p0 * temp[start_temp[j], 2]
                            temp[start_temp[j], 3] = t0_temp[j]
                            temp[end_temp[j], 4] = (1 - p1) * temp[
                                end_temp[j], 1
                            ] + p1 * temp[end_temp[j], 4]
                            temp[end_temp[j], 5] = (1 - p1) * temp[
                                end_temp[j], 2
                            ] + p1 * temp[end_temp[j], 5]
                            temp[end_temp[j], 6] = t1_temp[j]
                    else:
                        p0 = (temp[0, 6] - t0_temp) / (temp[0, 6] - temp[0, 3])
                        p1 = (t1_temp - temp[-1, 3]) / (temp[-1, 6] - temp[-1, 3])
                        temp[0, 1] = (1 - p0) * temp[0, 4] + p0 * temp[0, 1]
                        temp[0, 2] = (1 - p0) * temp[0, 5] + p0 * temp[0, 2]
                        temp[0, 3] = t0_temp
                        temp[-1, 4] = (1 - p1) * temp[-1, 1] + p1 * temp[-1, 4]
                        temp[-1, 5] = (1 - p1) * temp[-1, 2] + p1 * temp[-1, 5]
                        temp[-1, 6] = t1_temp

            obs_dur = sum((temp[:, 6] - temp[:, 3])[temp[:, 7] == 1])
            d_home_1 = great_circle_dist(home_x, home_y, temp[:, 1], temp[:, 2])
            d_home_2 = great_circle_dist(home_x, home_y, temp[:, 4], temp[:, 5])
            d_home = (d_home_1 + d_home_2) / 2
            max_dist_home = max(np.concatenate((d_home_1, d_home_2)))
            time_at_home = sum((temp[:, 6] - temp[:, 3])[d_home <= 50])
            mov_vec = np.round(
                great_circle_dist(temp[:, 4], temp[:, 5], temp[:, 1], temp[:, 2]), 0
            )
            flight_d_vec = mov_vec[temp[:, 0] == 1]
            flight_t_vec = (temp[:, 6] - temp[:, 3])[temp[:, 0] == 1]
            pause_t_vec = (temp[:, 6] - temp[:, 3])[temp[:, 0] == 2]
            total_pause_time = sum(pause_t_vec)
            total_flight_time = sum(flight_t_vec)
            dist_traveled = sum(mov_vec)
            # Locations of importance
            log_tags_temp = []
            if places_of_interest is not None or save_log:
                pause_vec = temp[temp[:, 0] == 2]
                pause_array = np.array([])
                for row in pause_vec:
                    if great_circle_dist(row[1], row[2], home_x, home_y) > 5:
                        if len(pause_array) == 0:
                            pause_array = np.array(
                                [[row[1], row[2], (row[6] - row[3]) / 60]]
                            )
                        elif (
                            np.min(
                                great_circle_dist(
                                    row[1], row[2], pause_array[:, 0], pause_array[:, 1]
                                )
                            )
                            > 5
                        ):
                            pause_array = np.append(
                                pause_array,
                                [[row[1], row[2], (row[6] - row[3]) / 60]],
                                axis=0,
                            )
                        else:
                            pause_array[
                                great_circle_dist(
                                    row[1], row[2], pause_array[:, 0], pause_array[:, 1]
                                )
                                <= 5,
                                -1,
                            ] += (row[6] - row[3]) / 60
                all_place_times_temp = [0 for _ in range(len(places_of_interest) + 1)]

                for pause_index, _ in enumerate(pause_array):
                    row = pause_array[pause_index]
                    if places_of_interest is not None:
                        add_to_other = True
                        for j, _ in enumerate(places_of_interest):
                            place = places_of_interest[j]
                            if place in ids.keys():
                                for element_id in ids[place]:
                                    if len(locations[element_id]) == 1:
                                        if (
                                            great_circle_dist(
                                                row[0],
                                                row[1],
                                                locations[element_id][0][0],
                                                locations[element_id][0][1],
                                            )
                                            < 7.5
                                        ):
                                            all_place_times_temp[j] += row[2] / 60
                                            add_to_other = False
                                            break
                                    elif len(locations[element_id]) >= 3:
                                        polygon = Polygon(locations[element_id])
                                        point = Point(row[0], row[1])
                                        if polygon.contains(point):
                                            all_place_times_temp[j] += row[2] / 60
                                            add_to_other = False
                                            break

                        # in case of pause not in places of interest
                        if add_to_other:
                            all_place_times_temp[-1] += row[2] / 60

                    if save_log:
                        if threshold is None:
                            threshold = 60
                            sys.stdout.write(
                                "threshold parameter set to None,"
                                + " automatically converted to 60min."
                                + "\n"
                            )
                        if row[2] >= threshold:
                            for location_index, _ in enumerate(locations.keys()):
                                element_id = list(locations.keys())[location_index]
                                values = list(locations.values())[location_index]

                                if len(values) == 1:
                                    if (
                                        great_circle_dist(
                                            row[0], row[1], values[0][0], values[0][1]
                                        )
                                        < 7.5
                                    ):
                                        log_tags_temp.append(tags[element_id])
                                elif len(values) >= 3:
                                    polygon = Polygon(values)
                                    point = Point(row[0], row[1])
                                    if polygon.contains(point):
                                        log_tags_temp.append(tags[element_id])

            if len(flight_d_vec) > 0:
                av_f_len = np.mean(flight_d_vec)
                sd_f_len = np.std(flight_d_vec)
                av_f_dur = np.mean(flight_t_vec)
                sd_f_dur = np.std(flight_t_vec)
            else:
                av_f_len = 0
                sd_f_len = 0
                av_f_dur = 0
                sd_f_dur = 0
            if len(pause_t_vec) > 0:
                av_p_dur = np.mean(pause_t_vec)
                sd_p_dur = np.std(pause_t_vec)
            else:
                av_p_dur = 0
                sd_p_dur = 0
            if option == "hourly":
                if obs_dur == 0:
                    res = [
                        year,
                        month,
                        day,
                        hour,
                        0,
                        np.nan,
                        np.nan,
                        np.nan,
                        np.nan,
                        np.nan,
                        np.nan,
                        np.nan,
                        np.nan,
                        np.nan,
                        np.nan,
                        np.nan,
                    ]
                    if places_of_interest is not None:
                        for p in range(len(places_of_interest) + 1):
                            res.append(np.nan)
                    summary_stats.append(res)
                    log_tags[
                        str(day)
                        + "/"
                        + str(month)
                        + "/"
                        + str(year)
                        + " "
                        + str(hour)
                        + ":00"
                    ] = np.nan
                else:
                    res = [
                        year,
                        month,
                        day,
                        hour,
                        obs_dur / 60,
                        time_at_home / 60,
                        dist_traveled,
                        max_dist_home,
                        total_flight_time / 60,
                        av_f_len,
                        sd_f_len,
                        av_f_dur / 60,
                        sd_f_dur / 60,
                        total_pause_time / 60,
                        av_p_dur / 60,
                        sd_p_dur / 60,
                    ]
                    if places_of_interest is not None:
                        res += all_place_times_temp
                    log_tags[
                        str(day)
                        + "/"
                        + str(month)
                        + "/"
                        + str(year)
                        + " "
                        + str(hour)
                        + ":00"
                    ] = log_tags_temp

                    summary_stats.append(res)
            if option == "daily":
                hours = []
                for j in range(temp.shape[0]):
                    time_list = stamp2datetime((temp[j, 3] + temp[j, 6]) / 2, tz_str)
                    hours.append(time_list[3])
                hours = np.array(hours)
                day_index = (hours >= 8) * (hours <= 19)
                night_index = np.logical_not(day_index)
                day_part = temp[day_index, :]
                night_part = temp[night_index, :]
                obs_day = sum((day_part[:, 6] - day_part[:, 3])[day_part[:, 7] == 1])
                obs_night = sum(
                    (night_part[:, 6] - night_part[:, 3])[night_part[:, 7] == 1]
                )
                temp_pause = temp[temp[:, 0] == 2, :]
                centroid_x = np.dot(
                    (temp_pause[:, 6] - temp_pause[:, 3]) / total_pause_time,
                    temp_pause[:, 1],
                )
                centroid_y = np.dot(
                    (temp_pause[:, 6] - temp_pause[:, 3]) / total_pause_time,
                    temp_pause[:, 2],
                )
                r_vec = great_circle_dist(
                    centroid_x, centroid_y, temp_pause[:, 1], temp_pause[:, 2]
                )
                radius = np.dot(
                    (temp_pause[:, 6] - temp_pause[:, 3]) / total_pause_time, r_vec
                )
                _, _, _, t_xy = num_sig_places(temp_pause, 50)
                num_sig = sum(np.array(t_xy) / 60 > 15)
                t_sig = np.array(t_xy)[np.array(t_xy) / 60 > 15]
                p = t_sig / sum(t_sig)
                entropy = -sum(p * np.log(p + 0.00001))
                if temp.shape[0] == 1:
                    diameter = 0
                else:
                    D = pairwise_great_circle_dist(temp[:, [1, 2]])
                    diameter = max(D)
                if obs_dur == 0:
                    res = [
                        year,
                        month,
                        day,
                        0,
                        0,
                        0,
                        np.nan,
                        np.nan,
                        np.nan,
                        np.nan,
                        np.nan,
                        np.nan,
                        np.nan,
                        np.nan,
                        np.nan,
                        np.nan,
                        np.nan,
                        np.nan,
                        np.nan,
                        np.nan,
                        np.nan,
                    ]
                    if places_of_interest is not None:
                        for p in range(len(places_of_interest) + 1):
                            res.append(np.nan)
                    summary_stats.append(res)
                    log_tags[str(day) + "/" + str(month) + "/" + str(year)] = np.nan
                else:
                    res = [
                        year,
                        month,
                        day,
                        obs_dur / 3600,
                        obs_day / 3600,
                        obs_night / 3600,
                        time_at_home / 3600,
                        dist_traveled / 1000,
                        max_dist_home / 1000,
                        radius / 1000,
                        diameter / 1000,
                        num_sig,
                        entropy,
                        total_flight_time / 3600,
                        av_f_len / 1000,
                        sd_f_len / 1000,
                        av_f_dur / 3600,
                        sd_f_dur / 3600,
                        total_pause_time / 3600,
                        av_p_dur / 3600,
                        sd_p_dur / 3600,
                    ]
                    if places_of_interest is not None:
                        res += all_place_times_temp
                    summary_stats.append(res)
                    if split_day_night:
                        if i % 2 == 0:
                            time_cat = "datetime"
                        else:
                            time_cat = "nighttime"
                        log_tags[
                            str(day)
                            + "/"
                            + str(month)
                            + "/"
                            + str(year)
                            + ", "
                            + time_cat
                        ] = log_tags_temp
                    else:
                        log_tags[
                            str(day) + "/" + str(month) + "/" + str(year)
                        ] = log_tags_temp
        summary_stats = pd.DataFrame(np.array(summary_stats))
        if places_of_interest is None:
            places_of_interest2 = []
        else:
            places_of_interest2 = places_of_interest.copy()
            places_of_interest2.append("other")
        if option == "hourly":
            summary_stats.columns = [
                "year",
                "month",
                "day",
                "hour",
                "obs_duration",
                "home_time",
                "dist_traveled",
                "max_dist_home",
                "total_flight_time",
                "av_flight_length",
                "sd_flight_length",
                "av_flight_duration",
                "sd_flight_duration",
                "total_pause_time",
                "av_pause_duration",
                "sd_pause_duration",
            ] + places_of_interest2
        if option == "daily":
            summary_stats.columns = [
                "year",
                "month",
                "day",
                "obs_duration",
                "obs_day",
                "obs_night",
                "home_time",
                "dist_traveled",
                "max_dist_home",
                "radius",
                "diameter",
                "num_sig_places",
                "entropy",
                "total_flight_time",
                "av_flight_length",
                "sd_flight_length",
                "av_flight_duration",
                "sd_flight_duration",
                "total_pause_time",
                "av_pause_duration",
                "sd_pause_duration",
            ] + places_of_interest2
    else:
        if places_of_interest is None:
            places_of_interest2 = []
        else:
            places_of_interest2 = places_of_interest.copy()
            places_of_interest2.append("other")
        if option == "hourly":
            summary_stats = pd.DataFrame(
                columns=[
                    "year",
                    "month",
                    "day",
                    "hour",
                    "obs_duration",
                    "home_time",
                    "dist_traveled",
                    "max_dist_home",
                    "total_flight_time",
                    "av_flight_length",
                    "sd_flight_length",
                    "av_flight_duration",
                    "sd_flight_duration",
                    "total_pause_time",
                    "av_pause_duration",
                    "sd_pause_duration",
                ]
                + places_of_interest2
            )
        if option == "daily":
            summary_stats = pd.DataFrame(
                columns=[
                    "year",
                    "month",
                    "day",
                    "obs_duration",
                    "obs_day",
                    "obs_night",
                    "home_time",
                    "dist_traveled",
                    "max_dist_home",
                    "radius",
                    "diameter",
                    "num_sig_places",
                    "entropy",
                    "total_flight_time",
                    "av_flight_length",
                    "sd_flight_length",
                    "av_flight_duration",
                    "sd_flight_duration",
                    "total_pause_time",
                    "av_pause_duration",
                    "sd_pause_duration",
                ]
                + places_of_interest2
            )

    if split_day_night:
        summary_stats_datetime = summary_stats[::2].reset_index(drop=True)
        summary_stats_nighttime = summary_stats[1::2].reset_index(drop=True)

        summary_stats2 = pd.concat(
            [summary_stats_datetime, summary_stats_nighttime.iloc[:, 3:]], axis=1
        )
        summary_stats2.columns = (
            list(summary_stats.columns)[:3]
            + [cname + "_datetime" for cname in list(summary_stats.columns)[3:]]
            + [cname + "_nighttime" for cname in list(summary_stats.columns)[3:]]
        )
        summary_stats2 = summary_stats2.drop(
            [
                "obs_day_datetime",
                "obs_night_datetime",
                "obs_day_nighttime",
                "obs_night_nighttime",
            ],
            axis=1,
        )
        summary_stats2.insert(
            3,
            "obs_duration",
            summary_stats2["obs_duration_datetime"]
            + summary_stats2["obs_duration_nighttime"],
        )
    else:
        summary_stats2 = summary_stats

    return summary_stats2, log_tags


def gps_quality_check(study_folder, id_code):
    """
    The function checks the gps data quality.
    Args: both study_folder and id_code should be string
    Return: a scalar between 0 and 1, bigger means better data quality
        (percentage of data which meet the criterion)
    """
    gps_path = study_folder + "/" + str(id_code) + "/gps"
    if not os.path.exists(gps_path):
        quality_check = 0
    else:
        file_list = os.listdir(gps_path)
        for i, _ in enumerate(file_list):
            if file_list[i][0] == ".":
                file_list[i] = file_list[i][2:]
        file_path = [gps_path + "/" + file_list[j] for j, _ in enumerate(file_list)]
        file_path = np.sort(np.array(file_path))
        # check if there are enough data for the following algorithm
        quality_yes = 0
        for i, _ in enumerate(file_path):
            df = pd.read_csv(file_path[i])
            if df.shape[0] > 60:
                quality_yes = quality_yes + 1
        quality_check = quality_yes / (len(file_path) + 0.0001)
    return quality_check


def gps_stats_main(
    study_folder,
    output_folder,
    tz_str,
    option,
    save_traj,
    places_of_interest=None,
    save_log=False,
    threshold=None,
    split_day_night=False,
    time_start=None,
    time_end=None,
    beiwe_id=None,
    parameters=None,
    all_memory_dict=None,
    all_bv_set=None,
):
    """
    This the main function to do the GPS imputation.
    It calls every function defined before.
    Args:   study_folder, string, the path of the study folder
            output_folder, string, the path of the folder
                where you want to save results
            tz_str, string, timezone
            option, 'daily' or 'hourly' or 'both'
                (resolution for summary statistics)
            save_traj, bool, True if you want to save the trajectories as a
                csv file, False if you don't
            places_of_interest: list of amenities or leisure places to watch,
                keywords as used in openstreetmaps
            save_log, bool, True if you want to output a log of locations
                visited and their tags
            threshold, int, time spent in a pause needs to exceed the
                threshold to be placed in the log
                only if save_log True, in minutes
            split_day_night, bool, True if you want to split all metrics to
                datetime and nighttime patterns
                only for daily option
            time_start, time_end are starting time and ending time of the
                window of interest
                time should be a list of integers with format
                [year, month, day, hour, minute, second]
                if time_start is None and time_end is None: then it reads all
                the available files
                if time_start is None and time_end is given, then it reads all
                the files before the given time
                if time_start is given and time_end is None, then it reads all
                the files after the given time
            beiwe_id: a list of beiwe IDs
            parameters: hyperparameters in functions, recommend to set it to
                none (by default)
            all_memory_dict and all_bv_set are dictionaries from previous run
                (none if it's the first time)
    Return: write summary stats as csv for each user during the specified
                period
            and a log of all locations visited as a json file if required
            and imputed trajectory if required
            and memory objects (all_memory_dict and all_bv_set)
                as pickle files for future use
            and a record csv file to show which users are processed
            and logger csv file to show warnings and bugs during the run
    """

    if isinstance(places_of_interest, list) and places_of_interest is not None:
        sys.stdout.write("Places of interest need to be of list type")
        sys.exit()

    if os.path.exists(output_folder) is False:
        os.mkdir(output_folder)

    if parameters is None:
        parameters = [
            60 * 60 * 24 * 10,
            60 * 60 * 24 * 30,
            0.002,
            200,
            5,
            1,
            0.3,
            0.2,
            0.5,
            100,
            0.01,
            0.05,
            3,
            10,
            2,
            "GLC",
            10,
            51,
            None,
            None,
            None,
        ]
    [
        l1,
        l2,
        l3,
        g,
        a1,
        a2,
        b1,
        b2,
        b3,
        d,
        sigma2,
        tol,
        switch,
        num,
        linearity,
        method,
        itrvl,
        accuracylim,
        r,
        w,
        h,
    ] = parameters
    pars0 = [l1, l2, l3, a1, a2, b1, b2, b3]
    pars1 = [l1, l2, a1, a2, b1, b2, b3, g]

    if r is None:
        orig_r = None
    if w is None:
        orig_w = None
    if h is None:
        orig_h = None

    # beiwe_id should be a list of str
    if beiwe_id is None:
        beiwe_id = os.listdir(study_folder)
    # create a record of processed user id_code and starting/ending time

    if all_memory_dict is None:
        all_memory_dict = {}
        for id_code in beiwe_id:
            all_memory_dict[str(id_code)] = None

    if all_bv_set is None:
        all_bv_set = {}
        for id_code in beiwe_id:
            all_bv_set[str(id_code)] = None

    if option == "both":
        if os.path.exists(output_folder + "/hourly") is False:
            os.mkdir(output_folder + "/hourly")
        if os.path.exists(output_folder + "/daily") is False:
            os.mkdir(output_folder + "/daily")
    if save_traj is True:
        if os.path.exists(output_folder + "/trajectory") is False:
            os.mkdir(output_folder + "/trajectory")

    if len(beiwe_id) > 0:
        for id_code in beiwe_id:
            sys.stdout.write("User: " + id_code + "\n")
            try:
                # data quality check
                quality = gps_quality_check(study_folder, id_code)
                if quality > 0.6:
                    # read data
                    sys.stdout.write("Read in the csv files ..." + "\n")
                    data, _, _ = read_data(
                        id_code, study_folder, "gps", tz_str, time_start, time_end
                    )
                    if orig_r is None:
                        r = itrvl
                    if orig_h is None:
                        h = r
                    if orig_w is None:
                        w = np.mean(data.accuracy)
                    # process data
                    mobmat1 = GPS2MobMat(data, itrvl, accuracylim, r, w, h)
                    mobmat2 = InferMobMat(mobmat1, itrvl, r)
                    out_dict = BV_select(
                        mobmat2,
                        sigma2,
                        tol,
                        d,
                        pars0,
                        all_memory_dict[str(id_code)],
                        all_bv_set[str(id_code)],
                    )
                    all_bv_set[str(id_code)] = bv_set = out_dict["BV_set"]
                    all_memory_dict[str(id_code)] = out_dict["memory_dict"]
                    imp_table = ImputeGPS(
                        mobmat2, bv_set, method, switch, num, linearity, tz_str, pars1
                    )
                    traj = Imp2traj(imp_table, mobmat2, itrvl, r, w, h)
                    # save all_memory_dict and all_bv_set
                    with open(output_folder + "/all_memory_dict.pkl", "wb") as f:
                        pickle.dump(all_memory_dict, f)
                    with open(output_folder + "/all_bv_set.pkl", "wb") as f:
                        pickle.dump(all_bv_set, f)
                    if save_traj is True:
                        pd_traj = pd.DataFrame(traj)
                        pd_traj.columns = [
                            "status",
                            "x0",
                            "y0",
                            "t0",
                            "x1",
                            "y1",
                            "t1",
                            "obs",
                        ]
                        dest_path = output_folder + "/trajectory/" + str(id_code) + ".csv"
                        pd_traj.to_csv(dest_path, index=False)
                    if option == "both":
                        summary_stats1, logs1 = gps_summaries(
                            traj,
                            tz_str,
                            "hourly",
                            places_of_interest,
                            save_log,
                            threshold,
                            split_day_night,
                        )
                        write_all_summaries(
                            id_code, summary_stats1, output_folder + "/hourly"
                        )
                        summary_stats2, logs2 = gps_summaries(
                            traj,
                            tz_str,
                            "daily",
                            places_of_interest,
                            save_log,
                            threshold,
                            split_day_night,
                        )
                        write_all_summaries(
                            id_code, summary_stats2, output_folder + "/daily"
                        )
                        if save_log:
                            with open(
                                output_folder + "/hourly/locations_logs.json", "w"
                            ) as hourly:
                                json.dump(logs1, hourly, indent=4)
                            with open(
                                output_folder + "/daily/locations_logs.json", "w"
                            ) as daily:
                                json.dump(logs2, daily, indent=4)
                    else:
                        summary_stats, logs = gps_summaries(
                            traj,
                            tz_str,
                            option,
                            places_of_interest,
                            save_log,
                            threshold,
                            split_day_night,
                        )
                        write_all_summaries(id_code, summary_stats, output_folder)
                        if save_log:
                            with open(
                                (output_folder + "/" + option + "/locations_logs.json"),
                                "w",
                            ) as loc:
                                json.dump(logs, loc, indent=4)
                else:
                    sys.stdout.write(
                        "GPS data are not collected"
                        + " or the data quality is too low."
                        + "\n"
                    )
            except:
                sys.stdout.write("An error occured when processing the data." + "\n")
                break
