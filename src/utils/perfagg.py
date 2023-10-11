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

import sys, os, shutil, glob, re
import numpy as np
import math
import warnings
import pandas as pd

prog = "omniperf"

# Per IP block max number of simulutaneous counters
# GFX IP Blocks
perfmon_config = {
    "vega10": {
        "SQ": 8,
        "TA": 2,
        "TD": 2,
        "TCP": 4,
        "TCC": 4,
        "CPC": 2,
        "CPF": 2,
        "SPI": 2,
        "GRBM": 2,
        "GDS": 4,
        "TCC_channels": 16,
    },
    "mi50": {
        "SQ": 8,
        "TA": 2,
        "TD": 2,
        "TCP": 4,
        "TCC": 4,
        "CPC": 2,
        "CPF": 2,
        "SPI": 2,
        "GRBM": 2,
        "GDS": 4,
        "TCC_channels": 16,
    },
    "mi100": {
        "SQ": 8,
        "TA": 2,
        "TD": 2,
        "TCP": 4,
        "TCC": 4,
        "CPC": 2,
        "CPF": 2,
        "SPI": 2,
        "GRBM": 2,
        "GDS": 4,
        "TCC_channels": 32,
    },
    "mi200": {
        "SQ": 8,
        "TA": 2,
        "TD": 2,
        "TCP": 4,
        "TCC": 4,
        "CPC": 2,
        "CPF": 2,
        "SPI": 2,
        "GRBM": 2,
        "GDS": 4,
        "TCC_channels": 32,
    },
    "mi300": {
        "SQ": 8,
        "TA": 2,
        "TD": 2,
        "TCP": 4,
        "TCC": 4,
        "CPC": 2,
        "CPF": 2,
        "SPI": 2,
        "GRBM": 2,
        "GDS": 4,
        "TCC_channels": 32,
    },
}


def test_df_column_equality(df):
    return df.eq(df.iloc[:, 0], axis=0).all(1).all()


# joins disparate runs less dumbly than rocprof
def join_prof(workload_dir, join_type, log_file, verbose, out=None):
    # Set default output directory if not specified
    if type(workload_dir) == str:
        if out is None:
            out = workload_dir + "/pmc_perf.csv"
        files = glob.glob(workload_dir + "/" + "pmc_perf_*.csv")
    elif type(workload_dir) == list:
        files = workload_dir
    else:
        print("ERROR: Invalid workload_dir")
        sys.exit(1)

    df = None
    for i, file in enumerate(files):
        _df = pd.read_csv(file) if type(workload_dir) == str else file
        if join_type == "kernel":
            key = _df.groupby("Kernel_Name").cumcount()
            _df["key"] = _df.Kernel_Name + " - " + key.astype(str)
        elif join_type == "grid":
            key = _df.groupby(["Kernel_Name", "GRD"]).cumcount()
            _df["key"] = (
                _df.Kernel_Name + " - " + _df.GRD.astype(str) + " - " + key.astype(str)
            )
        else:
            print("ERROR: Unrecognized --join-type")
            sys.exit(1)

        if df is None:
            df = _df
        else:
            # join by unique index of kernel
            df = pd.merge(df, _df, how="inner", on="key", suffixes=("", f"_{i}"))

    # TODO: check for any mismatch in joins
    duplicate_cols = {
        "GPU_ID": [col for col in df.columns if "GPU_ID" in col],
        "GRD": [col for col in df.columns if "GRD" in col],
        "WGR": [col for col in df.columns if "WGR" in col],
        "LDS": [col for col in df.columns if "LDS" in col],
        "SCR": [col for col in df.columns if "SCR" in col],
        "SGPR": [col for col in df.columns if "SGPR" in col],
    }
    # Check for vgpr counter in ROCm < 5.3
    if "vgpr" in df.columns:
        duplicate_cols["vgpr"] = [col for col in df.columns if "vgpr" in col]
    # Check for vgpr counter in ROCm >= 5.3
    else:
        duplicate_cols["Arch_VGPR"] = [col for col in df.columns if "Arch_VGPR" in col]
        duplicate_cols["ACCUM_VGPR"] = [col for col in df.columns if "ACCUM_VGPR" in col]
    for key, cols in duplicate_cols.items():
        _df = df[cols]
        if not test_df_column_equality(_df):
            msg = (
                "WARNING: Detected differing {} values while joining pmc_perf.csv".format(
                    key
                )
            )
            warnings.warn(msg)
            if log_file:
                log_file.write(msg + "\n")
        else:
            msg = "Successfully joined {} in pmc_perf.csv".format(key)
            if log_file:
                log_file.write(msg + "\n")
        if test_df_column_equality(_df) and verbose:
            print(msg)

    # now, we can:
    #   A) throw away any of the "boring" duplicats
    df = df[
        [
            k
            for k in df.keys()
            if not any(
                check in k
                for check in [
                    # removed merged counters, keep original
                    "GPU_ID_",
                    "GRD_",
                    "WGR_",
                    "LDS_",
                    "SCR_",
                    "vgpr_",
                    "Arch_VGPR_",
                    "ACCUM_VGPR",
                    "SGPR_",
                    "Dispatch_ID_",
                    # un-mergable, remove all
                    "Queue_ID",
                    "Queue_Index",
                    "PID",
                    "TID",
                    "SIG",
                    "OBJ",
                    # rocscope specific merged counters, keep original
                    "dispatch_",
                ]
            )
        ]
    ]
    #   B) any timestamps that are _not_ the duration, which is the one we care
    #   about
    df = df[
        [
            k
            for k in df.keys()
            if not any(
                check in k
                for check in [
                    "DispatchNs",
                    "CompleteNs",
                    # rocscope specific timestamp
                    "HostDuration",
                ]
            )
        ]
    ]
    #   C) sanity check the name and key
    namekeys = [k for k in df.keys() if "Kernel_Name" in k]
    assert len(namekeys)
    for k in namekeys[1:]:
        assert (df[namekeys[0]] == df[k]).all()
    df = df.drop(columns=namekeys[1:])
    # now take the median of the durations
    bkeys = []
    ekeys = []
    for k in df.keys():
        if "Start_Timestamp" in k:
            bkeys.append(k)
        if "End_Timestamp" in k:
            ekeys.append(k)
    # compute mean begin and end timestamps
    endNs = df[ekeys].mean(axis=1)
    beginNs = df[bkeys].mean(axis=1)
    # and replace
    df = df.drop(columns=bkeys)
    df = df.drop(columns=ekeys)
    df["BeginNs"] = beginNs
    df["EndNs"] = endNs
    # finally, join the drop key
    df = df.drop(columns=["key"])
    # save to file and delete old file(s), skip if we're being called outside of Omniperf
    if type(workload_dir) == str:
        df.to_csv(out, index=False)
        if not verbose:
            for file in files:
                os.remove(file)
    else:
        return df


def pmc_perf_split(workload_dir):
    workload_perfmon_dir = workload_dir + "/perfmon"
    lines = open(workload_perfmon_dir + "/pmc_perf.txt", "r").read().splitlines()

    # Iterate over each line in pmc_perf.txt
    mpattern = r"^pmc:(.*)"
    i = 0
    for line in lines:
        # Verify no comments
        stext = line.split("#")[0].strip()
        if not stext:
            continue

        # all pmc counters start with  "pmc:"
        m = re.match(mpattern, stext)
        if m is None:
            continue

        # Create separate file for each line
        fd = open(workload_perfmon_dir + "/pmc_perf_" + str(i) + ".txt", "w")
        fd.write(stext + "\n\n")
        fd.write("gpu:\n")
        fd.write("range:\n")
        fd.write("kernel:\n")
        fd.close()

        i += 1

    # Remove old pmc_perf.txt input from perfmon dir
    os.remove(workload_perfmon_dir + "/pmc_perf.txt")


def update_pmc_bucket(
    counters, save_file, soc, pmc_list=None, stext=None, workload_perfmon_dir=None
):
    # Verify inputs.
    # If save_file is True, we're being called internally, from perfmon_coalesce
    # Else we're being called externally, from rocomni
    detected_external_call = False
    if save_file and (stext is None or workload_perfmon_dir is None):
        raise ValueError(
            "stext and workload_perfmon_dir must be specified if save_file is True"
        )
    if pmc_list is None:
        detected_external_call = True
        pmc_list = dict(
            [
                ("SQ", []),
                ("GRBM", []),
                ("TCP", []),
                ("TA", []),
                ("TD", []),
                ("TCC", []),
                ("SPI", []),
                ("CPC", []),
                ("CPF", []),
                ("GDS", []),
                ("TCC2", {}),  # per-channel TCC perfmon
            ]
        )
        for ch in range(perfmon_config[soc]["TCC_channels"]):
            pmc_list["TCC2"][str(ch)] = []

    if "SQ_ACCUM_PREV_HIRES" in counters and not detected_external_call:
        # save  all level counters separately
        nindex = counters.index("SQ_ACCUM_PREV_HIRES")
        level_counter = counters[nindex - 1]

        if save_file:
            # Save to level counter file, file name = level counter name
            fd = open(workload_perfmon_dir + "/" + level_counter + ".txt", "w")
            fd.write(stext + "\n\n")
            fd.write("gpu:\n")
            fd.write("range:\n")
            fd.write("kernel:\n")
            fd.close()

        return pmc_list

    # save normal pmc counters in matching buckets
    for counter in counters:
        IP_block = counter.split(sep="_")[0].upper()
        # SQC and SQ belong to the IP block, coalesce them
        if IP_block == "SQC":
            IP_block = "SQ"

        if IP_block != "TCC":
            # Insert unique pmc counters into its bucket
            if counter not in pmc_list[IP_block]:
                pmc_list[IP_block].append(counter)

        else:
            # TCC counters processing
            m = re.match(r"[\s\S]+\[(\d+)\]", counter)
            if m is None:
                # Aggregated TCC counters
                if counter not in pmc_list[IP_block]:
                    pmc_list[IP_block].append(counter)

            else:
                # TCC channel ID
                ch = m.group(1)

                # fake IP block for per channel TCC
                if str(ch) in pmc_list["TCC2"]:
                    # append unique counter into the channel
                    if counter not in pmc_list["TCC2"][str(ch)]:
                        pmc_list["TCC2"][str(ch)].append(counter)
                else:
                    # initial counter in this channel
                    pmc_list["TCC2"][str(ch)] = [counter]

    if detected_external_call:
        # sort the per channel counter, so that same counter in all channels can be aligned
        for ch in range(perfmon_config[soc]["TCC_channels"]):
            pmc_list["TCC2"][str(ch)].sort()
    return pmc_list


def perfmon_coalesce(pmc_files_list, soc, workload_dir):
    workload_perfmon_dir = workload_dir + "/perfmon"

    # match pattern for pmc counters
    mpattern = r"^pmc:(.*)"
    pmc_list = dict(
        [
            ("SQ", []),
            ("GRBM", []),
            ("TCP", []),
            ("TA", []),
            ("TD", []),
            ("TCC", []),
            ("SPI", []),
            ("CPC", []),
            ("CPF", []),
            ("GDS", []),
            ("TCC2", {}),  # per-channel TCC perfmon
        ]
    )
    for ch in range(perfmon_config[soc]["TCC_channels"]):
        pmc_list["TCC2"][str(ch)] = []

    # Extract all PMC counters and store in separate buckets
    for fname in pmc_files_list:
        lines = open(fname, "r").read().splitlines()

        for line in lines:
            # Strip all comements, skip empty lines
            stext = line.split("#")[0].strip()
            if not stext:
                continue

            # all pmc counters start with  "pmc:"
            m = re.match(mpattern, stext)
            if m is None:
                continue

            # we have found all the counters, store them in buckets
            counters = m.group(1).split()

            # Utilitze helper function once a list of counters has be extracted
            save_file = True
            pmc_list = update_pmc_bucket(
                counters, save_file, soc, pmc_list, stext, workload_perfmon_dir
            )

    # add a timestamp file
    fd = open(workload_perfmon_dir + "/timestamps.txt", "w")
    fd.write("pmc:\n\n")
    fd.write("gpu:\n")
    fd.write("range:\n")
    fd.write("kernel:\n")
    fd.close()

    # sort the per channel counter, so that same counter in all channels can be aligned
    for ch in range(perfmon_config[soc]["TCC_channels"]):
        pmc_list["TCC2"][str(ch)].sort()

    return pmc_list


def perfmon_emit(pmc_list, soc, workload_dir=None):
    # Calculate the minimum number of iteration to save the pmc counters
    # non-TCC counters
    pmc_cnt = [
        len(pmc_list[key]) / perfmon_config[soc][key]
        for key in pmc_list
        if key not in ["TCC", "TCC2"]
    ]

    # TCC counters
    tcc_channels = perfmon_config[soc]["TCC_channels"]

    tcc_cnt = len(pmc_list["TCC"]) / perfmon_config[soc]["TCC"]
    tcc2_cnt = (
        np.array([len(pmc_list["TCC2"][str(ch)]) for ch in range(tcc_channels)])
        / perfmon_config[soc]["TCC"]
    )

    # Total number iterations to write pmc: counters line
    niter = max(math.ceil(max(pmc_cnt)), math.ceil(tcc_cnt) + math.ceil(max(tcc2_cnt)))

    # Emit PMC counters into pmc config file
    if workload_dir:
        workload_perfmon_dir = workload_dir + "/perfmon"
        fd = open(workload_perfmon_dir + "/pmc_perf.txt", "w")
    else:
        batches = []

    tcc2_index = 0
    for iter in range(niter):
        # Prefix
        line = "pmc: "

        # Add all non-TCC counters
        for key in pmc_list:
            if key not in ["TCC", "TCC2"]:
                N = perfmon_config[soc][key]
                ip_counters = pmc_list[key][iter * N : iter * N + N]
                if ip_counters:
                    line = line + " " + " ".join(ip_counters)

        # Add TCC counters
        N = perfmon_config[soc]["TCC"]
        tcc_counters = pmc_list["TCC"][iter * N : iter * N + N]

        if not tcc_counters:
            # TCC per-channel counters
            for ch in range(perfmon_config[soc]["TCC_channels"]):
                tcc_counters += pmc_list["TCC2"][str(ch)][
                    tcc2_index * N : tcc2_index * N + N
                ]

            tcc2_index += 1

        # TCC aggregated counters
        line = line + " " + " ".join(tcc_counters)
        if workload_dir:
            fd.write(line + "\n")
        else:
            b = line.split()
            b.remove("pmc:")
            batches.append(b)

    if workload_dir:
        fd.write("\ngpu:\n")
        fd.write("range:\n")
        fd.write("kernel:\n")
        fd.close()
    else:
        return batches


def perfmon_filter(workload_dir, perfmon_dir, args):
    workload_perfmon_dir = workload_dir + "/perfmon"
    soc = args.target

    # Initialize directories
    # TODO: Modify this so that data is appended to previous?
    if not os.path.isdir(workload_dir):
        os.makedirs(workload_dir)
    else:
        shutil.rmtree(workload_dir)

    os.makedirs(workload_perfmon_dir)

    ref_pmc_files_list = glob.glob(perfmon_dir + "/" + "pmc_*perf*.txt")
    ref_pmc_files_list += glob.glob(perfmon_dir + "/" + soc + "/pmc_*_perf*.txt")

    # Perfmon list filtering
    if args.ipblocks != None:
        for i in range(len(args.ipblocks)):
            args.ipblocks[i] = args.ipblocks[i].lower()
        mpattern = "pmc_([a-zA-Z0-9_]+)_perf*"

        pmc_files_list = []
        for fname in ref_pmc_files_list:
            fbase = os.path.splitext(os.path.basename(fname))[0]
            ip = re.match(mpattern, fbase).group(1)
            if ip in args.ipblocks:
                pmc_files_list.append(fname)
                print("fname: " + fbase + ": Added")
            else:
                print("fname: " + fbase + ": Skipped")

    else:
        # default: take all perfmons
        pmc_files_list = ref_pmc_files_list

    # Coalesce and writeback workload specific perfmon
    pmc_list = perfmon_coalesce(pmc_files_list, soc, workload_dir)
    perfmon_emit(pmc_list, soc, workload_dir)


def pmc_filter(workload_dir, perfmon_dir, soc):
    workload_perfmon_dir = workload_dir + "/perfmon"

    if not os.path.isdir(workload_perfmon_dir):
        os.makedirs(workload_perfmon_dir)
    else:
        shutil.rmtree(workload_perfmon_dir)

    ref_pmc_files_list = glob.glob(perfmon_dir + "/roofline/" + "pmc_roof_perf.txt")
    # ref_pmc_files_list += glob.glob(perfmon_dir + "/" + soc + "/pmc_*_perf*.txt")

    pmc_files_list = ref_pmc_files_list

    # Coalesce and writeback workload specific perfmon
    pmc_list = perfmon_coalesce(pmc_files_list, soc, workload_dir)
    perfmon_emit(pmc_list, soc, workload_dir)
