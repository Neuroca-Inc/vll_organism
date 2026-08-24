# Rung-0 Pairwise Routing Battery

- Source: `00_MCR_Origin.md`
- Related: `01_MCR_Transition-to-Formal.md`
- Frozen tick: 3600
- Eligible targets: 21
- Node q95 passes: 12/21
- Natural multi-hop q95 passes: 7/16
- BH-FDR q<=0.05: 12/21
- Holm p<=0.05: 12/21
- Heat-scale robustness: PASS (max |Δfraction|=0.018916)
- Active-budget saturation: 0/22 targets
- Zero-diffusion negative control: PASS
- Source creation-order nonoverlap: True

## Omnibus controls
- all_reachable: {"calibration_controls": 10000, "evaluation_controls": 10000, "label": "all_reachable", "max_delta_null_q95": 0.2537920508853696, "max_delta_p_right": 9.999000099990002e-05, "median_delta_null_q95": 0.07953970148713949, "median_delta_p_right": 9.999000099990002e-05, "pass_count_null_q95": 9.0, "pass_count_p_right": 0.0084991500849915, "real_max_delta": 0.7501639555063883, "real_median_delta": 0.4285633591724426, "real_pass_count": 12, "status": "OK", "targets": 21}
- natural_multihop: {"calibration_controls": 10000, "evaluation_controls": 10000, "label": "natural_multihop", "max_delta_null_q95": 0.2537920508853696, "max_delta_p_right": 9.999000099990002e-05, "median_delta_null_q95": 0.11531550241784996, "median_delta_p_right": 0.08749125087491251, "pass_count_null_q95": 7.0, "pass_count_p_right": 0.051394860513948606, "real_max_delta": 0.7501639555063883, "real_median_delta": 0.09220294102693039, "real_pass_count": 7, "status": "OK", "targets": 16}
- one_hop: {"calibration_controls": 10000, "evaluation_controls": 10000, "label": "one_hop", "max_delta_null_q95": 0.21794296487873, "max_delta_p_right": 9.999000099990002e-05, "median_delta_null_q95": 0.08508650310812911, "median_delta_p_right": 9.999000099990002e-05, "pass_count_null_q95": 2.0, "pass_count_p_right": 0.0086991300869913, "real_max_delta": 0.7139183802132112, "real_median_delta": 0.5913619298689308, "real_pass_count": 5, "status": "OK", "targets": 5}
- two_hop: {"calibration_controls": 10000, "evaluation_controls": 10000, "label": "two_hop", "max_delta_null_q95": 0.25214640233685737, "max_delta_p_right": 9.999000099990002e-05, "median_delta_null_q95": 0.24605977469430318, "median_delta_p_right": 9.999000099990002e-05, "pass_count_null_q95": 3.0, "pass_count_p_right": 0.007099290070992901, "real_max_delta": 0.7501639555063883, "real_median_delta": 0.7439325207914749, "real_pass_count": 7, "status": "OK", "targets": 8}
- three_plus_hop: {"calibration_controls": 10000, "evaluation_controls": 10000, "label": "three_plus_hop", "max_delta_null_q95": 0.1436934268774485, "max_delta_p_right": 0.16578342165783422, "median_delta_null_q95": 0.12928112740771333, "median_delta_p_right": 0.3240675932406759, "pass_count_null_q95": 4.0, "pass_count_p_right": 1.0, "real_max_delta": 0.08352577730910601, "real_median_delta": 0.03146187142809429, "real_pass_count": 0, "status": "OK", "targets": 8}

## Degree-matched control
- {"all_reachable": {"calibration_controls": 10000, "evaluation_controls": 10000, "label": "degree_matched_all", "max_delta_null_q95": 0.26499125948968444, "max_delta_p_right": 9.999000099990002e-05, "median_delta_null_q95": 0.0807255405805173, "median_delta_p_right": 9.999000099990002e-05, "pass_count_null_q95": 9.0, "pass_count_p_right": 0.007999200079992, "real_max_delta": 0.7327918293458142, "real_median_delta": 0.4139859493584829, "real_pass_count": 12, "status": "OK", "targets": 21}, "degree_match_movable_fraction": 0.884375, "natural_multihop": {"calibration_controls": 10000, "evaluation_controls": 10000, "label": "degree_matched_multihop", "max_delta_null_q95": 0.26499125948968444, "max_delta_p_right": 9.999000099990002e-05, "median_delta_null_q95": 0.10989418891754617, "median_delta_p_right": 0.0908909109089091, "pass_count_null_q95": 7.0, "pass_count_p_right": 0.0542945705429457, "real_max_delta": 0.7327918293458142, "real_median_delta": 0.08697671737446594, "real_pass_count": 7, "status": "OK", "targets": 16}}
