# Release gate: fail closed on timing, unconstrained internal paths or DRC.
source [file join $root scripts rf_cdc_waivers.tcl]
report_methodology -file [file join $report_dir methodology.rpt]
report_timing_summary -delay_type min_max -report_unconstrained -check_timing_verbose \
    -file [file join $report_dir timing_summary_routed.rpt]
report_timing -delay_type min -max_paths 20 -file [file join $report_dir hold_paths.rpt]
report_drc -file [file join $report_dir drc.rpt]
report_route_status -file [file join $report_dir route_status.rpt]
report_clock_interaction -file [file join $report_dir clock_interaction.rpt]
source [file join $root scripts check_rf_bus_skew.tcl]
set cdc_text [report_cdc -details -return_string]
set cdc_file [open [file join $report_dir cdc.rpt] w]
puts $cdc_file $cdc_text
close $cdc_file
write_waivers -force [file join $report_dir cdc_waivers.xdc]
if {[regexp {CDC-[0-9]+[ \t]+Critical[ \t]+[1-9]} $cdc_text]} {
    error "Release refused: an unreviewed critical CDC remains"
}
report_utilization -file [file join $report_dir utilization.rpt]
set timing_text [report_timing_summary -delay_type min_max -check_timing_verbose -return_string]
if {![string match {*All user specified timing constraints are met.*} $timing_text]} {
    error "Release refused: routed setup/hold/pulse-width timing is not met"
}
foreach check {no_clock unconstrained_internal_endpoints no_input_delay partial_input_delay partial_output_delay loops latch_loops} {
    if {![regexp "checking $check \\(0\\)" $timing_text]} {error "Release refused: check_timing $check is not zero"}
}
# check_timing includes asynchronous false-pathed LEDs in its output-delay
# count. Require exactly the four named LEDs and zero unconstrained outputs.
if {![string match {*checking no_output_delay (4)*} $timing_text] ||
    ![string match {*There are 0 ports with no output delay specified.*} $timing_text] ||
    ![string match {*There are 4 ports with no output delay but user has a false path constraint*} $timing_text]} {
    error "Release refused: unexpected unconstrained output ports"
}
set methodology_text [report_methodology -return_string]
if {[regexp {Critical Warning|[ \t]Error[ \t]} $methodology_text]} {
    error "Release refused: critical methodology violation remains"
}
set bad_drc [get_drc_violations -quiet -filter {SEVERITY == Error || SEVERITY == {Critical Warning}}]
if {[llength $bad_drc]} {error "Release refused: DRC errors/critical warnings: $bad_drc"}
set_property BITSTREAM.GENERAL.COMPRESS true [current_design]
