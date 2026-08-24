# Rung-0 Pairwise Routing Battery

- Source: `01_MCR_Transition-to-Formal.md`
- Related: `00_MCR_Origin.md`
- Frozen tick: 3600
- Eligible targets: 39
- Node q95 passes: 16/39
- Natural multi-hop q95 passes: 5/28
- BH-FDR q<=0.05: 13/39
- Holm p<=0.05: 7/39
- Heat-scale robustness: PASS (max |Δfraction|=0.001330)
- Active-budget saturation: 0/39 targets
- Zero-diffusion negative control: PASS
- Source creation-order nonoverlap: True

## Omnibus controls
- all_reachable: {"calibration_controls": 10000, "evaluation_controls": 10000, "label": "all_reachable", "max_delta_null_q95": 0.1583312032486922, "max_delta_p_right": 9.999000099990002e-05, "median_delta_null_q95": 0.050574538822173054, "median_delta_p_right": 0.033596640335966405, "pass_count_null_q95": 12.0, "pass_count_p_right": 0.019498050194980503, "real_max_delta": 0.5753204739044959, "real_median_delta": 0.0586325270846651, "real_pass_count": 20, "status": "OK", "targets": 39}
- natural_multihop: {"calibration_controls": 10000, "evaluation_controls": 10000, "label": "natural_multihop", "max_delta_null_q95": 0.14897396842202348, "max_delta_p_right": 0.0496950304969503, "median_delta_null_q95": 0.05210120773037185, "median_delta_p_right": 0.2683731626837316, "pass_count_null_q95": 9.0, "pass_count_p_right": 0.0542945705429457, "real_max_delta": 0.14926075924303572, "real_median_delta": 0.017842388688457583, "real_pass_count": 9, "status": "OK", "targets": 28}
- one_hop: {"calibration_controls": 10000, "evaluation_controls": 10000, "label": "one_hop", "max_delta_null_q95": 0.11555180714911086, "max_delta_p_right": 9.999000099990002e-05, "median_delta_null_q95": 0.06948015485927218, "median_delta_p_right": 9.999000099990002e-05, "pass_count_null_q95": 4.0, "pass_count_p_right": 0.0025997400259974, "real_max_delta": 0.5753204739044959, "real_median_delta": 0.3050618375953798, "real_pass_count": 11, "status": "OK", "targets": 11}
- two_hop: {"calibration_controls": 10000, "evaluation_controls": 10000, "label": "two_hop", "max_delta_null_q95": 0.1103839493810852, "max_delta_p_right": 0.009099090090990901, "median_delta_null_q95": 0.05052692225085295, "median_delta_p_right": 0.025597440255974404, "pass_count_null_q95": 5.049999999999272, "pass_count_p_right": 0.0374962503749625, "real_max_delta": 0.14926075924303572, "real_median_delta": 0.061770188460295306, "real_pass_count": 8, "status": "OK", "targets": 16}
- three_plus_hop: {"calibration_controls": 10000, "evaluation_controls": 10000, "label": "three_plus_hop", "max_delta_null_q95": 0.14748714669134608, "max_delta_p_right": 0.32556744325567444, "median_delta_null_q95": 0.07200072005237702, "median_delta_p_right": 0.36626337366263373, "pass_count_null_q95": 5.0, "pass_count_p_right": 0.14728527147285272, "real_max_delta": 0.0586325270846651, "real_median_delta": 0.013062435244268304, "real_pass_count": 1, "status": "OK", "targets": 12}

## Degree-matched control
- {"all_reachable": {"calibration_controls": 10000, "evaluation_controls": 10000, "label": "degree_matched_all", "max_delta_null_q95": 0.15204895706189356, "max_delta_p_right": 9.999000099990002e-05, "median_delta_null_q95": 0.03655334766957813, "median_delta_p_right": 0.0026997300269973002, "pass_count_null_q95": 10.0, "pass_count_p_right": 0.0053994600539946005, "real_max_delta": 0.5537003436352199, "real_median_delta": 0.07344223971904747, "real_pass_count": 23, "status": "OK", "targets": 39}, "degree_match_movable_fraction": 0.8481848184818482, "natural_multihop": {"calibration_controls": 10000, "evaluation_controls": 10000, "label": "degree_matched_multihop", "max_delta_null_q95": 0.14158504426729226, "max_delta_p_right": 0.040995900409959006, "median_delta_null_q95": 0.04042630043178596, "median_delta_p_right": 0.101989801019898, "pass_count_null_q95": 8.0, "pass_count_p_right": 0.019498050194980503, "real_max_delta": 0.1493267463610941, "real_median_delta": 0.029255489627455944, "real_pass_count": 12, "status": "OK", "targets": 28}}
