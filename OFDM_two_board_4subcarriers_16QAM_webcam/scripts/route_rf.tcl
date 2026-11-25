# Route the placed and physically optimized design, then run the release checks.
set root [file normalize [file join [file dirname [info script]] ..]]
set build_dir [file join $root build hardware]
set report_dir [file join $root reports implementation_qam16]
set out_dir [file join $build_dir artifacts]
set_param general.maxThreads 4
set checkpoint [file join $build_dir vivado ofdm4.runs impl_1 rf_system_wrapper_physopt.dcp]
open_checkpoint $checkpoint
route_design -directive Explore
write_checkpoint -force [file join $build_dir ofdm_qam16_routed.dcp]
source [file join $root scripts verify_rf_timing.tcl]
write_bitstream -force [file join $out_dir ofdm_qam16.bit]
file copy -force [file join $build_dir vivado ofdm4.gen sources_1 bd rf_system hw_handoff rf_system.hwh] [file join $out_dir ofdm_qam16.hwh]
puts "RF_BUILD_PASS=$out_dir"
