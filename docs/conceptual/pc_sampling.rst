.. meta::
   :description: ROCm Compute Profiler: using PC sampling
   :keywords: ROCm Compute Profiler, PC sampling

******************
Using PC sampling in ROCm Compute Profiler
******************

PC (Program Counter) sampling service for GPU profiling is a profiling
technique to periodically sample the program counter during GPU kernel
execution. PC sampling helps in understanding code execution patterns
and identifying hotspot(s).

ROCm Compute Profiler supports Host Trap PC sampling (>=MI200) and
Stochastic (Hardware-Based) PC Sampling (>=MI300). Stochastic PC sampling
delivers additional information that tells whether a sampled wave issued an
instruction represented with particular PC. If not, it provides the reason
for not issuing the instruction (stall reason). This type of information is
particularly useful for understanding stalls during the kernel execution.

Profiling options:
    --pc-sampling-method: stochastic or host_trap
    --pc-sampling-interval:
        For stochastic sampling, the interval is in cycles.
        The finest granularity is 1 cycle.

        For host_trap sampling, the interval is in microsecond (DEFAULT: 1048576).
        The interval should be power of 2. Recommend to try to start from 1048576,
        and try decreasing until 65536.

Analysis options:
        --pc-sampling-sorting-type: offset or count (DEFAULT: offset).
        "offset" is assembly instruction offset in the code object.

To associate PC sampling info back to HIP source code, users need to build the
profiling target app with -g to keep the symbols. Otherwise, PC sampling info
would be only associated with assembly lines.

Note that PC sampling feature is BETA version at this moment. To enable PC sampling,
users have to explicitly enable it with block index 21. Here is the example cmd:
Profiling:
.. code-block:: bash
rocprof-compute profile -n pc_test -b 21 --no-roof --pc-sampling-method stochastic --pc-sampling-interval 1048576 -VVV -- target_app
Analysis:
.. code-block:: bash
rocprof-compute analyze -p workloads/pc_test/MI300A_A1/ -b 21 -k 0 --pc-sampling-sorting-type offset
