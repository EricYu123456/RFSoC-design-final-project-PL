# In Vivado's Tcl Console: source {C:/.../scripts/open_waves.tcl}
set root [file normalize [file join [file dirname [info script]] ..]]
set wave_dir [file join $root reports mixed_4]
if {![file exists [file join $wave_dir waves.wdb]]} {
    error "Run python scripts/run.py --case mixed --packets 4 --waves first"
}
open_wave_database [file join $wave_dir waves.wdb]
create_wave_config ook_arq
foreach signal {
    clk rst dut/data_wave dut/ack_wave dut/data_energy dut/ack_energy
    dut/data_bit dut/data_bit_valid dut/ack_bit dut/ack_bit_valid
    dut/sender/state dut/sender/packet_id dut/sender/retry_count dut/sender/timer
    dut/receiver/state dut/receiver/unique_packets
} {
    add_wave /tb_image_link/$signal
}
save_wave_config [file join $wave_dir waves.wcfg]
puts "WAVE_CONFIG_READY=[file join $wave_dir waves.wcfg]"
if {[info exists argv] && [lsearch -exact $argv --verify]>=0} {exit}
