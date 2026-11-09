# Export the hardware description for a bitstream already written and checked.
set root [file normalize [file join [file dirname [info script]] ..]]
set build_dir [file join $root build rf]
set out_dir [file join $root board output]
if {![file exists [file join $out_dir ook_rf.bit]]} {error "Verified bitstream is missing"}
open_project [file join $build_dir vivado ook_rf.xpr]
if {![llength [get_files -quiet rf_clock_constraints.tcl]]} {
    add_files -fileset utils_1 [list [file join $root scripts rf_clock_constraints.tcl]]
}
file mkdir [file join $out_dir hardware_description]
write_hw_platform -fixed -force -file [file join $out_dir hardware_description ook_rf.xsa]
file copy -force [file join $build_dir vivado ook_rf.gen sources_1 bd rf_system hw_handoff rf_system.hwh] [file join $out_dir ook_rf.hwh]
puts "RF_BUILD_PASS=$out_dir"
exit
