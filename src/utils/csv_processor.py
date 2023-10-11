##############################################################################bl
# MIT License
#
# Copyright (c) 2021 - 2023 Advanced Micro Devices, Inc. All Rights Reserved.
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
##############################################################################el

import argparse
import collections
import os
import subprocess
import sys
import re
import pandas as pd
import getpass
from pymongo import MongoClient
from tqdm import tqdm
import glob
from common import resolve_rocprof

cache = dict()

supported_arch = {"gfx906": "mi50", "gfx908": "mi100", "gfx90a": "mi200"}
MAX_SERVER_SEL_DELAY = 5000  # 5 sec connection timeout


# Note: shortener is now dependent on a rocprof install with llvm
def kernel_name_shortener(workload_dir, level):
    def shorten_file(df, level):
        global cache

        columnName = ""
        if "KernelName" in df:
            columnName = "KernelName"
        if "Name" in df:
            columnName = "Name"

        if columnName == "KernelName" or columnName == "Name":
            # loop through all indices
            for index in df.index:
                original_name = df.loc[index, columnName]
                if original_name in cache:
                    continue

                cmd = [llvm_filt, original_name]

                proc = subprocess.Popen(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE
                )

                demangled_name, e = proc.communicate()
                demangled_name = str(demangled_name, "UTF-8").strip()

                # cache miss, add the shortened name to the dictionary
                new_name = ""
                matches = ""

                names_and_args = re.compile(
                    r"(?P<name>[( )A-Za-z0-9_]+)([ ,*<>()]+)(::)?"
                )

                # works for name Kokkos::namespace::init_lock_array_kernel_threadid(int) [clone .kd]
                if names_and_args.search(demangled_name):
                    matches = names_and_args.findall(demangled_name)
                else:
                    # Works for first case  '__amd_rocclr_fillBuffer.kd'
                    cache[original_name] = new_name
                    if new_name == None or new_name == "":
                        cache[original_name] = demangled_name
                    continue

                current_level = 0
                for name in matches:
                    ##can cause errors if a function name or argument is equal to 'clone'
                    if name[0] == "clone":
                        continue
                    if len(name) == 3:
                        if name[2] == "::":
                            continue

                    if current_level < level:
                        new_name += name[0]
                    # closing '>' is to be taken account by the while loop
                    if name[1].count(">") == 0:
                        if current_level < level:
                            if not (
                                current_level == level - 1 and name[1].count("<") > 0
                            ):
                                new_name += name[1]
                        current_level += name[1].count("<")

                    curr_index = 0
                    # cases include '>'  '> >, ' have to go in depth here to not lose account of commas and current level
                    while name[1].count(">") > 0 and curr_index < len(name[1]):
                        if current_level < level:
                            new_name += name[1][curr_index:]
                            current_level -= name[1][curr_index:].count(">")
                            curr_index = len(name[1])
                        elif name[1][curr_index] == (">"):
                            current_level -= 1
                        curr_index += 1

                cache[original_name] = new_name
                if new_name == None or new_name == "":
                    cache[original_name] = demangled_name

            df[columnName] = df[columnName].map(cache)

        return df

    # Only shorten if valid shortening level
    if level < 5:
        returnPath = True
        rocprof_path = resolve_rocprof(returnPath)
        # Given expected rocprof dir format (ie '/opt/rocm-x.x.x/bin/rocprof') navigate to llvm in parent
        rocm_dir = os.path.abspath(os.path.join(rocprof_path, os.pardir, os.pardir))
        # TODO: Unfix rocm_dir
        rocm_dir = os.path.abspath("/opt/rocm")
        llvm_filt = os.path.join(rocm_dir, "llvm", "bin", "llvm-cxxfilt")
        if not os.path.isfile(llvm_filt):
            print(
                "Error: Could not resolve llvm-cxxfilt in rocm install: {}".format(
                    llvm_filt
                )
            )
            sys.exit(0)

        for fpath in glob.glob(workload_dir + "/*.csv"):
            try:
                orig_df = pd.read_csv(
                    fpath,
                    on_bad_lines="skip",
                    engine="python",
                )
                modified_df = shorten_file(orig_df, level)
                modified_df.to_csv(fpath, index=False)
            except pd.errors.EmptyDataError:
                print("Skipping empty csv " + str(fpath))

        print("KernelName shortening complete!")


# Verify target directory and setup connection
def parse(args, profileAndExport):
    host = args.host
    port = str(args.port)
    username = args.username

    if profileAndExport:
        workload = args.workload + "/" + args.target + "/"
    else:
        workload = args.workload

    # Verify directory path is valid
    print("Pulling data from ", workload)
    if os.path.isdir(workload):
        print("The directory exists")
    else:
        raise argparse.ArgumentTypeError("Directory does not exist")

    sysInfoPath = workload + "/sysinfo.csv"
    if os.path.isfile(sysInfoPath):
        print("Found sysinfo file")
        sysInfo = pd.read_csv(sysInfoPath)
        # Extract SoC
        arch = sysInfo["gpu_soc"][0]
        soc = supported_arch[arch]
        # Extract name
        name = sysInfo["workload_name"][0]
    else:
        print("Unable to parse SoC or workload name from sysinfo.csv")
        sys.exit(1)

    db = "omniperf_" + str(args.team) + "_" + str(name) + "_" + soc

    if args.password == "":
        try:
            password = getpass.getpass()
        except Exception as error:
            print("PASSWORD ERROR", error)
        else:
            print("Password recieved")
    else:
        password = args.password

    if db.find(".") != -1 or db.find("-") != -1:
        raise ValueError("'-' and '.' are not permited in workload name", db)

    connectionInfo = {
        "username": username,
        "password": password,
        "host": host,
        "port": port,
        "workload": workload,
        "db": db,
    }

    return connectionInfo


def convert_folder(connectionInfo):
    # Test connection
    connection_str = (
        "mongodb://"
        + connectionInfo["username"]
        + ":"
        + connectionInfo["password"]
        + "@"
        + connectionInfo["host"]
        + ":"
        + connectionInfo["port"]
        + "/?authSource=admin"
    )
    client = MongoClient(connection_str, serverSelectionTimeoutMS=MAX_SERVER_SEL_DELAY)
    try:
        client.server_info()
    except:
        print("ERROR: Unable to connect to the server")
        sys.exit(1)

    i = 0
    file = "blank"
    for file in tqdm(os.listdir(connectionInfo["workload"])):
        if file.endswith(".csv"):
            print(connectionInfo["workload"] + "/" + file)
            try:
                fileName = file[0 : file.find(".")]
                cmd = (
                    "mongoimport --quiet --uri mongodb://{}:{}@{}:{}/{}?authSource=admin --file {} -c {} --drop --type csv --headerline"
                ).format(
                    connectionInfo["username"],
                    connectionInfo["password"],
                    connectionInfo["host"],
                    connectionInfo["port"],
                    connectionInfo["db"],
                    connectionInfo["workload"] + "/" + file,
                    fileName,
                )
                os.system(cmd)
                i += 1
            except pd.errors.EmptyDataError:
                print("Skipping empty csv " + file)

    mydb = client["workload_names"]
    mycol = mydb["names"]
    value = {"name": connectionInfo["db"]}
    newValue = {"name": connectionInfo["db"]}
    mycol.replace_one(value, newValue, upsert=True)
    print("{} collections added.".format(i))
    print("Workload name uploaded")
