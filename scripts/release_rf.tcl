# Re-validate an existing routed design after constraint-only corrections.
set root [file normalize [file join [file dirname [info script]] ..]]
set build_dir [file join $root build rf]
set report_dir [file join $root reports implementation_rf]
set out_dir [file join $root board output]
open_project [file join $build_dir vivado ook_rf.xpr]
set stale_clock_xdc [get_files -quiet rfsoc4x2_rf_clocks.xdc]
if {[llength $stale_clock_xdc]} {remove_files $stale_clock_xdc}
set_property STEPS.OPT_DESIGN.TCL.PRE [file join $root scripts rf_clock_constraints.tcl] [get_runs impl_1]
open_run impl_1
source [file join $root scripts rf_clock_constraints.tcl]
source [file join $root scripts verify_rf_timing.tcl]
write_checkpoint -force [file join $build_dir ook_rf_routed.dcp]
write_bitstream -force [file join $out_dir ook_rf.bit]
file mkdir [file join $out_dir hardware_description]
write_hw_platform -fixed -force -file [file join $out_dir hardware_description ook_rf.xsa]
file copy -force [file join $build_dir vivado ook_rf.gen sources_1 bd rf_system hw_handoff rf_system.hwh] [file join $out_dir ook_rf.hwh]
puts "RF_BUILD_PASS=$out_dir"
exit
