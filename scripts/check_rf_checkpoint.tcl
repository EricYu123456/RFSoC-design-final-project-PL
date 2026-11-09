set root [file normalize [file join [file dirname [info script]] ..]]
set report_dir [file join $root reports implementation_rf]
open_checkpoint [file join $root build rf ook_rf_routed.dcp]
source [file join $root scripts check_rf_bus_skew.tcl]
exit
