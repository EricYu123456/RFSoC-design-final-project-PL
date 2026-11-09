# Exercise the same saved defaults used by Run Behavioral Simulation in the GUI.
set root [file normalize [file join [file dirname [info script]] ..]]
open_project [file join $root build vivado ook_image_link.xpr]
launch_simulation -mode behavioral
close_sim
exit
