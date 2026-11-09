set root [file normalize [file join [file dirname [info script]] ..]]
open_project [file join $root build vivado ook_image_link.xpr]
file mkdir [file join $root reports synthesis]
foreach run [get_runs] {
    if {[regexp -nocase {error|failed} [get_property STATUS $run]]} {reset_run $run}
}
reset_run synth_1
if {[catch {
    launch_runs synth_1 -jobs 4
    wait_on_run synth_1
    if {[get_property PROGRESS [get_runs synth_1]] ne "100%"} {error "Synthesis did not complete"}
    open_run synth_1
    report_utilization -file [file join $root reports synthesis utilization.rpt]
    report_timing_summary -file [file join $root reports synthesis timing_summary.rpt]
    write_checkpoint -force [file join $root build image_link_synth.dcp]
} reason options]} {
    set report [open [file join $root reports synthesis status.md] w]
    puts $report "# Synthesis did not complete\n\n$reason\n\nSee build/synthesis.log for the full error. XSim test results are separate.\n\nLicense setup: ../../docs/vivado_license_setup.md"
    close $report
    return -options $options $reason
}
set report [open [file join $root reports synthesis status.md] w]
puts $report "# Synthesis completed\n\nTarget: xczu48dr-ffvg1517-2-e; Vivado 2024.2.\n\nSee utilization.rpt and timing_summary.rpt. These are synthesis estimates, before placement and routing."
close $report
exit
