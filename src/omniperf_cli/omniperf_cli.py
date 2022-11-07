#!/usr/bin/env python3

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

"""
    Quick run:
        omniperf_cli.py -d 1st_run_dir -d 2nd_run_dir -b 2

    Common abbreviations in the code:
        df - pandas.dataframe
        pmc - HW performance conuter
        metric - derived expression from pmc and soc spec
"""

import sys



import copy
import sys
import argparse
import os.path
from pathlib import Path


def omniperf_cli(args):
    cur_root = Path(__file__).resolve()

    if args.dependency:
        print("pip3 install astunparse numpy tabulate pandas pyyaml")
        sys.exit(0)

    import pandas as pd
    from collections import OrderedDict
    from dataclasses import dataclass, field
    from tabulate import tabulate

    # from utils import schema, parser, file_io, tty, plot
    from omniperf_cli.utils import schema, parser, file_io, tty

    # Fixme: cur_root.parent.joinpath('soc_params')
    soc_params_dir = os.path.join(os.path.dirname(__file__), "..", "soc_params")

    soc_spec_df = file_io.load_soc_params(soc_params_dir)

    # NB: maybe create bak file for the old run before open it
    output = open(args.output_file, "w+") if args.output_file else sys.stdout

    single_panel_config = file_io.is_single_panel_config(Path(args.config_dir))
    archConfigs = {}
    for arch in file_io.supported_arch.keys():
        ac = schema.ArchConfig()
        if args.list_kernels:
            ac.panel_configs = file_io.top_stats_build_in_config
        else:
            arch_panel_config = (
                args.config_dir if single_panel_config else args.config_dir.joinpath(arch)
            )
            ac.panel_configs = file_io.load_panel_configs(arch_panel_config)

        # TODO: filter_metrics should/might be one per arch
        # print(ac)

        parser.build_dfs(ac, args.filter_metrics)

        archConfigs[arch] = ac

    if args.list_metrics in file_io.supported_arch.keys():
        print(
            tabulate(
                pd.DataFrame.from_dict(
                    archConfigs[args.list_metrics].metric_list,
                    orient="index",
                    columns=["Metric"],
                ),
                headers="keys",
                tablefmt="fancy_grid",
            ),
            file=output,
        )
        sys.exit(0)

    for k, v in archConfigs.items():
        parser.build_metric_value_string(v.dfs, v.dfs_type, args.normal_unit)

    runs = OrderedDict()

    # err checking for multiple runs and multiple gpu_kernel filter
    # TODO: move it to util
    if args.gpu_kernel and (len(args.path) != len(args.gpu_kernel)):
        if len(args.gpu_kernel) == 1:
            for i in range(len(args.path) - 1):
                args.gpu_kernel.extend(args.gpu_kernel)
        else:
            print(
                "Error: the number of --filter-kernels doesn't match the number of --dir.",
                file=output,
            )
            sys.exit(-1)

    # Todo: warning single -d with multiple dirs
    for d in args.path:
        w = schema.Workload()
        w.sys_info = file_io.load_sys_info(Path(d[0], "sysinfo.csv"))
        w.avail_ips = w.sys_info["ip_blocks"].item().split("|")
        arch = w.sys_info.iloc[0]["gpu_soc"]
        w.dfs = copy.deepcopy(archConfigs[arch].dfs)
        w.dfs_type = archConfigs[arch].dfs_type
        w.soc_spec = file_io.get_soc_params(soc_spec_df, arch)
        runs[d[0]] = w

    # Filtering
    if args.gpu_kernel:
        for d, gk in zip(args.path, args.gpu_kernel):
            for k_idx in gk:
                if int(k_idx) >= 10:
                    print(
                        "{} is an invalid kernel filter. Must be between 0-9.".format(
                            k_idx
                        )
                    )
                    sys.exit(2)
            runs[d[0]].filter_kernel_ids = gk

    if args.gpu_id:
        if len(args.gpu_id) == 1 and len(args.path) != 1:
            for i in range(len(args.path) - 1):
                args.gpu_id.extend(args.gpu_id)
        for d, gi in zip(args.path, args.gpu_id):
            runs[d[0]].filter_gpu_ids = gi
    # NOTE: INVALID DISPATCH IDS ARE NOT CAUGHT HERE. THEY CAUSE AN ERROR IN TABLE GENERATION!!!!!!!!!
    if args.gpu_dispatch_id:
        if len(args.gpu_dispatch_id) == 1 and len(args.path) != 1:
            for i in range(len(args.path) - 1):
                args.gpu_dispatch_id.extend(args.gpu_dispatch_id)
        for d, gd in zip(args.path, args.gpu_dispatch_id):
            runs[d[0]].filter_dispatch_ids = gd

    if args.gui:
        import dash
        from omniperf_cli.utils import gui
        import dash_bootstrap_components as dbc

        app = dash.Dash(__name__, external_stylesheets=[dbc.themes.CYBORG])

        if len(runs) == 1:
            num_results = 10
            file_io.create_df_kernel_top_stats(
                args.path[0][0],
                runs[args.path[0][0]].filter_gpu_ids,
                runs[args.path[0][0]].filter_dispatch_ids,
                args.time_unit,
                num_results,
            )
            runs[args.path[0][0]].raw_pmc = file_io.create_df_pmc(
                args.path[0][0]
            )  # create mega df
            is_gui = False
            # parser.load_table_data(
            #     runs[args.path[0][0]], args.path[0][0], is_gui, args.g
            # )  # create the loaded table

            input_filters = {
                "kernel": runs[args.path[0][0]].filter_kernel_ids,
                "gpu": runs[args.path[0][0]].filter_gpu_ids,
                "dispatch": runs[args.path[0][0]].filter_dispatch_ids,
            }

            gui.build_layout(
                app,
                runs,
                archConfigs["gfx90a"],
                input_filters,
                args.decimal,
                args.time_unit,
                args.cols,
                str(args.path[0][0]),
                args.g,
                args.verbose,
            )
            app.run_server(debug=False, host="0.0.0.0", port=args.gui)
        else:
            print("Multiple runs not supported yet")
    else:
        # NB:
        # If we assume the panel layout for all archs are similar, it doesn't matter
        # which archConfig passed into show_all function.
        # After decide to how to manage kernels display patterns, we can revisit it.
        for d in args.path:
            num_results = 10
            file_io.create_df_kernel_top_stats(
                d[0],
                runs[d[0]].filter_gpu_ids,
                runs[d[0]].filter_dispatch_ids,
                args.time_unit,
                num_results,
            )
            runs[d[0]].raw_pmc = file_io.create_df_pmc(d[0])  # creates mega dataframe
            is_gui = False
            parser.load_table_data(
                runs[d[0]], d[0], is_gui, args.g
            )  # create the loaded table
        if args.list_kernels:
            tty.show_kernels(runs, archConfigs["gfx90a"], output, args.decimal)
        else:
            tty.show_all(
                runs,
                archConfigs["gfx90a"],
                output,
                args.decimal,
                args.time_unit,
                args.cols,
            )
