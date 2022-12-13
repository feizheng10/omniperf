################################################################################
# Copyright (c) 2021 - 2022 Advanced Micro Devices, Inc. All rights reserved.
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.  IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.
################################################################################

import ast
import sys
import astunparse
import re
import os
from matplotlib.pyplot import axis
import pandas as pd
import numpy as np
from tabulate import tabulate
from omniperf_analyze.utils import schema

# ------------------------------------------------------------------------------
# Internal global definitions

# NB:
# Ammolite is unique gemstone from the Rocky Mountains.
# "ammolite__" is a special internal prefix to mark build-in global variables
# calculated or parsed from raw data sources. Its range is only in this file.
# Any other general prefixes string, like "buildin__", might be used by the
# editor. Whenever change it to a new one, replace all appearances in this file.

# 001 is ID of pmc_kernel_top.csv table
pmc_kernel_top_table_id = 1

# Build-in $denom defined in mongodb query:
#       "denom": {
#              "$switch" : {
#                 "branches": [
#                    {
#                         "case":  { "$eq": [ $normUnit, "per Wave"]} ,
#                         "then":  "&SQ_WAVES"
#                    },
#                    {
#                         "case":  { "$eq": [ $normUnit, "per Cycle"]} ,
#                         "then":  "&GRBM_GUI_ACTIVE"
#                    },
#                    {
#                         "case":  { "$eq": [ $normUnit, "per Sec"]} ,
#                         "then":  {"$divide":[{"$subtract": ["&EndNs", "&BeginNs" ]}, 1000000000]}
#                    }
#                 ],
#                "default": 1
#              }
#       }
supported_denom = {
    "per_wave": "SQ_WAVES",
    "per_cycle": "GRBM_GUI_ACTIVE",
    "per_second": "((EndNs - BeginNs) / 1000000000)",
    "per_kernel": "1",
}

# Build-in defined in mongodb variables:
build_in_vars = {
    "numActiveCUs": "TO_INT(MIN((((ROUND(AVG(((4 * SQ_BUSY_CU_CYCLES) / GRBM_GUI_ACTIVE)), \
              0) / $maxWavesPerCU) * 8) + MIN(MOD(ROUND(AVG(((4 * SQ_BUSY_CU_CYCLES) \
              / GRBM_GUI_ACTIVE)), 0), $maxWavesPerCU), 8)), $numCU))",
    "kernelBusyCycles": "ROUND(AVG((((EndNs - BeginNs) / 1000) * $sclk)), 0)",
}

supported_call = {
    # If the below has single arg, like(expr), it is a aggr, in which turn to a pd function.
    # If it has args like list [], in which turn to a python function.
    "MIN": "to_min",
    "MAX": "to_max",
    # simple aggr
    "AVG": "to_avg",
    "MEDIAN": "to_median",
    # functions apply to whole column of df or a single value
    "TO_INT": "to_int",
    # Support the below with 2 inputs
    "ROUND": "to_round",
    "MOD": "to_mod",
    # Concat operation from the memory chart "active cus"
    "CONCAT": "to_concat",
}

# ------------------------------------------------------------------------------


def to_min(*args):
    if len(args) == 1 and isinstance(args[0], pd.core.series.Series):
        return args[0].min()
    elif min(args) == None:
        return np.nan
    else:
        return min(args)


def to_max(*args):
    if len(args) == 1 and isinstance(args[0], pd.core.series.Series):
        return args[0].max()
    elif max(args) == None:
        return np.nan
    else:
        return max(args)


def to_avg(a):
    if str(type(a)) == "<class 'NoneType'>":
        return np.nan
    elif a.empty:
        return np.nan
    elif isinstance(a, pd.core.series.Series):
        return a.mean()
    else:
        raise Exception("to_avg: unsupported type.")


def to_median(a):
    if isinstance(a, pd.core.series.Series):
        return a.median()
    else:
        raise Exception("to_median: unsupported type.")


def to_int(a):
    if str(type(a)) == "<class 'NoneType'>":
        return np.nan
    elif isinstance(a, (int, float, np.int64)):
        return int(a)
    elif isinstance(a, pd.core.series.Series):
        return a.astype("Int64")
    # Do we need it?
    # elif isinstance(a, str):
    #     return int(a)
    else:
        raise Exception("to_int: unsupported type.")


def to_round(a, b):
    if isinstance(a, pd.core.series.Series):
        return a.round(b)
    else:
        return round(a, b)


def to_mod(a, b):
    if isinstance(a, pd.core.series.Series):
        return a.mod(b)
    else:
        return a % b


def to_concat(a, b):
    return str(a) + str(b)


class CodeTransformer(ast.NodeTransformer):
    """
    Python AST visitor to transform user defined equation string to df format
    """

    def visit_Call(self, node):
        self.generic_visit(node)
        # print("--- debug visit_Call --- ", node.args, node.func)
        # print(astunparse.dump(node))
        # print(astunparse.unparse(node))
        if isinstance(node.func, ast.Name):
            if node.func.id in supported_call:
                node.func.id = supported_call[node.func.id]
            else:
                raise Exception(
                    "Unknown call:", node.func.id
                )  # Could be removed if too strict
        return node

    def visit_IfExp(self, node):
        self.generic_visit(node)
        # print("visit_IfExp", type(node.test), type(node.body), type(node.orelse), dir(node))

        if isinstance(node.body, ast.Num):
            raise Exception(
                "Don't support body of IF with number only! Has to be expr with df['column']."
            )

        new_node = ast.Expr(
            value=ast.Call(
                func=ast.Attribute(value=node.body, attr="where", ctx=ast.Load()),
                args=[node.test, node.orelse],
                keywords=[],
            )
        )
        # print("-------------")
        # print(astunparse.dump(new_node))
        # print("-------------")

        return new_node

    # NB:
    # visit_Name is for replacing HW counter to its df expr. In this way, we
    # could support any HW counter names, which is easier than regex.
    #
    # There are 2 limitations:
    #   - It is not straightforward to support types other than simple column
    #     in df, such as [], (). If we need to support those, have to implement
    #     in correct way or work around.
    #   - The 'raw_pmc_df' is hack code. For other data sources, like wavefront
    #     data,We need to think about template or pass it as a parameter.
    def visit_Name(self, node):
        self.generic_visit(node)
        # print("-------------", node.id)
        if (not node.id.startswith("ammolite__")) and (not node.id in supported_call):
            new_node = ast.Subscript(
                value=ast.Name(id="raw_pmc_df", ctx=ast.Load()),
                slice=ast.Index(value=ast.Str(s=node.id)),
                ctx=ast.Load(),
            )

            node = new_node
        return node


def build_eval_string(equation, coll_level):
    """
    Convert user defined equation string to eval executable string
    For example,
        input: AVG(100  * SQ_ACTIVE_INST_SCA / ( GRBM_GUI_ACTIVE * $numCU ))
        output: to_avg(100 * raw_pmc_df["pmc_perf"]["SQ_ACTIVE_INST_SCA"] / \
                 (raw_pmc_df["pmc_perf"]["GRBM_GUI_ACTIVE"] * numCU))
        input: AVG(((TCC_EA_RDREQ_LEVEL_31 / TCC_EA_RDREQ_31) if (TCC_EA_RDREQ_31 != 0) else (0)))
        output: to_avg((raw_pmc_df["pmc_perf"]["TCC_EA_RDREQ_LEVEL_31"] / raw_pmc_df["pmc_perf"]["TCC_EA_RDREQ_31"]).where(raw_pmc_df["pmc_perf"]["TCC_EA_RDREQ_31"] != 0, 0))
        We can not handle the below for now,
        input: AVG((0 if (TCC_EA_RDREQ_31 == 0) else (TCC_EA_RDREQ_LEVEL_31 / TCC_EA_RDREQ_31)))
        But potential workaound is,
        output: to_avg(raw_pmc_df["pmc_perf"]["TCC_EA_RDREQ_31"].where(raw_pmc_df["pmc_perf"]["TCC_EA_RDREQ_31"] == 0, raw_pmc_df["pmc_perf"]["TCC_EA_RDREQ_LEVEL_31"] / raw_pmc_df["pmc_perf"]["TCC_EA_RDREQ_31"]))
    """

    if coll_level is None:
        raise Exception("Error: coll_level can not be None.")

    if not equation:
        return ""

    s = str(equation)
    # print("input:", s)

    # build-in variable starts with '$', python can not handle it.
    # replace '$' with 'ammolite__'.
    # TODO: pre-check there is no "ammolite__" in all config files.
    s = re.sub("\$", "ammolite__", s)

    # convert equation string to intermediate expression in df array format
    ast_node = ast.parse(s)
    # print(astunparse.dump(ast_node))
    transformer = CodeTransformer()
    transformer.visit(ast_node)

    s = astunparse.unparse(ast_node)

    # correct column name/label in df with [], such as TCC_HIT[0],
    # the target is df['TCC_HIT[0]']
    s = re.sub(r"\'\]\[(\d+)\]", r"[\g<1>]']", s)
    # use .get() to catch any potential KeyErrors
    s = re.sub("raw_pmc_df\['(.*?)']", r'raw_pmc_df.get("\1")', s)
    # apply coll_level
    s = re.sub(r"raw_pmc_df", "raw_pmc_df.get('" + coll_level + "')", s)
    # print("--- build_eval_string, return: ", s)
    return s


def update_denom_string(equation, unit):
    """
    Update $denom in equation with runtime nomorlization unit.
    """
    if not equation:
        return ""

    s = str(equation)

    if unit in supported_denom.keys():
        s = re.sub(r"\$denom", supported_denom[unit], s)

    return s


def update_normUnit_string(equation, unit):
    """
    Update $normUnit in equation with runtime nomorlization unit.
    It is string replacement for display only.
    """

    # TODO: We might want to do it for subtitle contains $normUnit
    if not equation:
        return ""

    return re.sub(
        "\((?P<PREFIX>\w*)\s+\+\s+(\$normUnit\))",
        "\g<PREFIX> " + re.sub("_", " ", unit),
        str(equation),
    ).capitalize()


def build_dfs(archConfigs, filter_metrics):
    """
    - Build dataframe for each type of data source within each panel.
      Each dataframe will be used as a template to load data with each run later.
      For now, support "metric_table" and "raw_csv_table". Otherwise, put an empty df.
    - Collect/build metric_list to suport customrized metrics profiling.
    """

    # TODO: more error checking for filter_metrics!!
    # if filter_metrics:
    #     for metric in filter_metrics:
    #         if not metric in avail_ip_blocks:
    #             print("{} is not a valid metric to filter".format(metric))
    #             exit(1)
    d = {}
    metric_list = {}
    dfs_type = {}
    for panel_id, panel in archConfigs.panel_configs.items():
        for data_source in panel["data source"]:
            for type, data_cofig in data_source.items():
                if type == "metric_table":
                    headers = ["Index"]
                    for key, tile in data_cofig["header"].items():
                        if key != "tips":
                            headers.append(tile)
                    headers.append("coll_level")

                    if "tips" in data_cofig["header"].keys():
                        headers.append(data_cofig["header"]["tips"])

                    df = pd.DataFrame(columns=headers)

                    i = 0
                    for key, entries in data_cofig["metric"].items():

                        data_source_idx = (
                            str(data_cofig["id"] // 100)
                            + "."
                            + str(data_cofig["id"] % 100)
                        )
                        metric_idx = data_source_idx + "." + str(i)
                        values = []

                        if (
                            (not filter_metrics)
                            or (metric_idx in filter_metrics)  # no filter
                            or  # metric in filter
                            # the whole table in filter
                            (data_source_idx in filter_metrics)
                            or
                            # the whole IP block in filter
                            (str(panel_id // 100) in filter_metrics)
                        ):

                            values.append(metric_idx)
                            values.append(key)
                            for k, v in entries.items():
                                if k != "tips" and k != "coll_level" and k != "alias":
                                    values.append(v)

                            if "alias" in entries.keys():
                                values.append(entries["alias"])

                            if "coll_level" in entries.keys():
                                values.append(entries["coll_level"])
                            else:
                                values.append(schema.pmc_perf_file_prefix)

                            if "tips" in entries.keys():
                                values.append(entries["tips"])

                            # print(key, entries)
                            df_new_row = pd.DataFrame([values], columns=headers)
                            df = pd.concat([df, df_new_row])

                        # collect metric_list
                        metric_list[metric_idx] = key.replace(" ", "_")
                        i += 1

                    df.set_index("Index", inplace=True)
                    # df.set_index('Metric', inplace=True)
                    # print(tabulate(df, headers='keys', tablefmt='fancy_grid'))
                elif type == "raw_csv_table":
                    data_source_idx = str(data_cofig["id"] // 100)
                    if (
                        (not filter_metrics)
                        or (data_source_idx == "0")  # no filter
                        or (data_source_idx in filter_metrics)
                    ):

                        if (
                            "columnwise" in data_cofig
                            and data_cofig["columnwise"] == True
                        ):
                            df = pd.DataFrame(
                                [data_cofig["source"]], columns=["from_csv_columnwise"]
                            )
                        else:
                            df = pd.DataFrame(
                                [data_cofig["source"]], columns=["from_csv"]
                            )
                        metric_list[data_source_idx] = panel["title"]
                    else:
                        df = pd.DataFrame()
                else:
                    df = pd.DataFrame()

                d[data_cofig["id"]] = df
                dfs_type[data_cofig["id"]] = type

    setattr(archConfigs, "dfs", d)
    setattr(archConfigs, "metric_list", metric_list)
    setattr(archConfigs, "dfs_type", dfs_type)


def build_metric_value_string(dfs, dfs_type, normal_unit):
    """
    Apply the real eval string to its field in the metric_table df.
    """

    for id, df in dfs.items():
        if dfs_type[id] == "metric_table":
            for expr in df.columns:
                if expr in schema.supported_field:
                    # NB: apply all build-in before building the whole string
                    df[expr] = df[expr].apply(update_denom_string, unit=normal_unit)

                    # NB: there should be a faster way to do with single apply
                    if not df.empty:
                        for i in range(df.shape[0]):
                            row_idx_label = df.index.to_list()[i]
                            # print(i, "row_idx_label", row_idx_label, expr)
                            if expr.lower() != "alias":
                                df.at[row_idx_label, expr] = build_eval_string(
                                    df.at[row_idx_label, expr],
                                    df.at[row_idx_label, "coll_level"],
                                )

                elif expr.lower() == "unit" or expr.lower() == "units":
                    df[expr] = df[expr].apply(update_normUnit_string, unit=normal_unit)

        # print(tabulate(df, headers='keys', tablefmt='fancy_grid'))


def eval_metric(dfs, dfs_type, sys_info, soc_spec, raw_pmc_df, debug):
    """
    Execute the expr string for each metric in the df.
    """

    # NB:
    #  Following with Omniperf 0.2.0, we are using HW spec from sys_info instead.
    #  The soc_spec is not in using right now, but can be used to do verification
    #  aganist sys_info, forced theoretical evaluation, or supporting tool-chains
    #  broken.
    ammolite__numSE = sys_info.numSE
    ammolite__numCU = sys_info.numCU
    ammolite__numSIMD = sys_info.numSIMD
    ammolite__numWavesPerCU = sys_info.maxWavesPerCU  # todo: check do we still need it
    ammolite__numSQC = sys_info.numSQC
    ammolite__L2Banks = sys_info.L2Banks
    ammolite__freq = sys_info.cur_sclk  # todo: check do we still need it
    ammolite__mclk = sys_info.cur_mclk
    ammolite__sclk = sys_info.sclk
    ammolite__maxWavesPerCU = sys_info.maxWavesPerCU
    ammolite__hbmBW = sys_info.hbmBW

    # TODO: fix all $normUnit in Unit column or title

    # build and eval all derived build-in global variables
    ammolite__build_in = {}
    for key, value in build_in_vars.items():
        # NB: assume all build in vars from pmc_perf.csv for now
        s = build_eval_string(value, schema.pmc_perf_file_prefix)
        try:
            ammolite__build_in[key] = eval(compile(s, "<string>", "eval"))
        except TypeError:
            ammolite__build_in[key] = None
        except AttributeError as ae:
            if ae == "'NoneType' object has no attribute 'get'":
                ammolite__build_in[key] = None

    ammolite__numActiveCUs = ammolite__build_in["numActiveCUs"]
    ammolite__kernelBusyCycles = ammolite__build_in["kernelBusyCycles"]

    # Hmmm... apply + lambda should just work
    # df['Value'] = df['Value'].apply(lambda s: eval(compile(str(s), '<string>', 'eval')))
    for id, df in dfs.items():
        if dfs_type[id] == "metric_table":
            for idx, row in df.iterrows():
                for expr in df.columns:
                    if expr in schema.supported_field:
                        if expr.lower() != "alias":
                            if row[expr]:
                                if debug:  # debug won't impact the regular calc
                                    print("~" * 40 + "\nExpression:")
                                    print(expr, "=", row[expr])
                                    print("Inputs:")
                                    matched_vars = re.findall("ammolite__\w+", row[expr])
                                    if matched_vars:
                                        for v in matched_vars:
                                            print(
                                                "Var ",
                                                v,
                                                ":",
                                                eval(compile(v, "<string>", "eval")),
                                            )
                                    matched_cols = re.findall(
                                        "raw_pmc_df\['\w+'\]\['\w+'\]", row[expr]
                                    )
                                    if matched_cols:
                                        for c in matched_cols:
                                            m = re.match(
                                                "raw_pmc_df\['(\w+)'\]\['(\w+)'\]", c
                                            )
                                            t = raw_pmc_df[m.group(1)][
                                                m.group(2)
                                            ].to_list()
                                            print(c)
                                            print(
                                                raw_pmc_df[m.group(1)][
                                                    m.group(2)
                                                ].to_list()
                                            )
                                            # print(
                                            #     tabulate(raw_pmc_df[m.group(1)][
                                            #         m.group(2)],
                                            #              headers='keys',
                                            #              tablefmt='fancy_grid'))
                                    print("\nOutput:")
                                    try:
                                        print(
                                            eval(compile(row[expr], "<string>", "eval"))
                                        )
                                        print("~" * 40)
                                    except TypeError:
                                        print(
                                            "skiping entry. Encounterd a missing counter"
                                        )
                                        print(expr, " has been assigned to None")
                                        print(np.nan)
                                    except AttributeError as ae:
                                        if (
                                            str(ae)
                                            == "'NoneType' object has no attribute 'get'"
                                        ):
                                            print(
                                                "skiping entry. Encounterd a missing csv"
                                            )
                                            print(np.nan)
                                        else:
                                            print(ae)
                                            sys.exit(1)

                                # print("eval_metric", id, expr)
                                try:
                                    out = eval(compile(row[expr], "<string>", "eval"))
                                    if row.name != "19.1.1" and np.isnan(
                                        out
                                    ):  # Special exception for unique format of Active CUs in mem chart
                                        row[expr] = ""
                                    else:
                                        row[expr] = out
                                except TypeError:
                                    row[expr] = ""
                                except AttributeError as ae:
                                    if (
                                        str(ae)
                                        == "'NoneType' object has no attribute 'get'"
                                    ):
                                        row[expr] = ""
                                    else:
                                        print(ae)
                                        sys.exit(1)

                            else:
                                # If not insert nan, the whole col might be treated
                                # as string but not nubmer if there is NONE
                                row[expr] = ""

            # print(tabulate(df, headers='keys', tablefmt='fancy_grid'))


def apply_filters(workload, is_gui, debug):
    """
    Apply user's filters to the raw_pmc df.
    """

    # TODO: error out properly if filters out of bound
    ret_df = workload.raw_pmc

    if workload.filter_gpu_ids:
        ret_df = ret_df.loc[
            ret_df[schema.pmc_perf_file_prefix]["gpu-id"]
            .astype(str)
            .isin([workload.filter_gpu_ids])
        ]
        if ret_df.empty:
            print("{} is an invalid gpu-id".format(workload.filter_gpu_ids))
            sys.exit(1)

    # NB:
    # Kernel id is unique!
    # We pick up kernel names from kerne ids first.
    # Then filter valid entries with kernel names.
    if workload.filter_kernel_ids:
        # There are two ways Kernel filtering is done:
        # 1) CLI accepts an array of ints, representing indexes of kernels from the pmc_kernel_top.csv
        # 2) GUI will be passing an array of strs. The full names of kernels as selected from dropdown
        if not is_gui:
            if debug:
                print("CLI kernel filtering")
            kernels = []
            # NB: mark selected kernels with "*"
            #    Todo: fix it for unaligned comparison
            kernel_top_df = workload.dfs[pmc_kernel_top_table_id]
            kernel_top_df["S"] = ""
            for kernel_id in workload.filter_kernel_ids:
                # print("------- ", kernel_id)
                kernels.append(kernel_top_df.loc[kernel_id, "KernelName"])
                kernel_top_df.loc[kernel_id, "S"] = "*"

            if kernels:
                # print("fitlered df:", len(df.index))
                ret_df = ret_df.loc[
                    ret_df[schema.pmc_perf_file_prefix]["KernelName"].isin(kernels)
                ]
        else:
            if debug:
                print("GUI kernel filtering")
            ret_df = ret_df.loc[
                ret_df[schema.pmc_perf_file_prefix]["KernelName"].isin(
                    workload.filter_kernel_ids
                )
            ]

    if workload.filter_dispatch_ids:
        # NB: support ignoring the 1st n dispatched execution by '> n'
        #     The better way may be parsing python slice string
        for d in workload.filter_dispatch_ids:
            if int(d) > len(ret_df) - 2:  # subtract 2 bc of the two header rows
                print("{} is an invalid dispatch id.".format(d))
                sys.exit(1)
        if ">" in workload.filter_dispatch_ids[0]:
            m = re.match("\> (\d+)", workload.filter_dispatch_ids[0])
            ret_df = ret_df[
                ret_df[schema.pmc_perf_file_prefix]["Index"] > int(m.group(1))
            ]
        else:
            ret_df = ret_df.loc[
                ret_df[schema.pmc_perf_file_prefix]["Index"]
                .astype(str)
                .isin(workload.filter_dispatch_ids)
            ]
    if debug:
        print("~" * 40, "\nraw pmc df info:\n")
        print(workload.raw_pmc.info())
        print("~" * 40, "\nfiltered pmc df info:")
        print(ret_df.info())

    return ret_df


def load_kernel_top(workload, dir):
    # NB:
    #   - Do pmc_kernel_top.csv loading before eval_metric because we need the kernel names.
    #   - There might be a better way/timing to load raw_csv_table.
    tmp = {}
    for id, df in workload.dfs.items():
        if "from_csv" in df.columns:
            tmp[id] = pd.read_csv(os.path.join(dir, df.loc[0, "from_csv"]))
        elif "from_csv_columnwise" in df.columns:
            # NB:
            #   Another way might be doing transpose in tty like metric_table.
            #   But we need to figure out headers and comparison properly.
            tmp[id] = pd.read_csv(
                os.path.join(dir, df.loc[0, "from_csv_columnwise"])
            ).transpose()
            # NB:
            #   All transposed columns should be marked with a general header,
            #   so tty could detect them and show them correctly in comparison.
            tmp[id].columns = ["Info"]
    workload.dfs.update(tmp)


def load_table_data(workload, dir, is_gui, debug, verbose):
    """
    Load data for all "raw_csv_table".
    Calculate mertric value for all "metric_table".
    """
    load_kernel_top(workload, dir)

    eval_metric(
        workload.dfs,
        workload.dfs_type,
        workload.sys_info.iloc[0],
        workload.soc_spec,
        apply_filters(workload, is_gui, debug),
        debug,
    )

    # Save workload
    name = "saved_analysis"
    out_path = os.path.join(dir, name)
    try:
        os.mkdir(out_path)
        if verbose >= 1:
            print("Created a Saved Analysis folder")
    except OSError as error:
        if verbose >= 1:
            print("Saved Analysis folder exists")
    for id, df in workload.dfs.items():
        if "coll_level" in list(df.columns):
            df = df.drop(["coll_level", "Tips"], axis=1)
        df.to_csv(os.path.join(out_path, str(id) + ".csv"), index=False)


def build_comparable_columns(time_unit):
    """
    Build comparable columns/headers for display
    """
    comparable_columns = schema.supported_field
    top_stat_base = ["Count", "Sum", "Mean", "Median"]

    for h in top_stat_base:
        comparable_columns.append(h + "(" + time_unit + ")")

    return comparable_columns
