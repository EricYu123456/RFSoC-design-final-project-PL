# Used both by the release gate and for checking the final routed checkpoint.
set bus_skew_text [report_bus_skew -warn_on_violation -return_string]
set bus_skew_file [open [file join $report_dir bus_skew.rpt] w]
puts $bus_skew_file $bus_skew_text
close $bus_skew_file
if {[regexp {Slack[ \t]+\(VIOLATED\)} $bus_skew_text] ||
    ![regexp {Slack[ \t]+\(MET\)} $bus_skew_text]} {
    error "Release refused: bus skew did not pass"
}
puts "RF_BUS_SKEW_PASS"
