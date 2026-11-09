if {[info exists ::env(OOK_WAVES)] && $::env(OOK_WAVES) eq "1"} {
    log_wave /tb_image_link/clk /tb_image_link/rst /tb_image_link/dut/sample_phase
    log_wave /tb_image_link/dut/data_wave /tb_image_link/dut/ack_wave
    log_wave /tb_image_link/dut/data_energy /tb_image_link/dut/ack_energy
    log_wave /tb_image_link/dut/data_bit /tb_image_link/dut/data_bit_valid
    log_wave /tb_image_link/dut/ack_bit /tb_image_link/dut/ack_bit_valid
    log_wave /tb_image_link/dut/sender/state /tb_image_link/dut/sender/packet_id
    log_wave /tb_image_link/dut/sender/retry_count /tb_image_link/dut/sender/timer
    log_wave /tb_image_link/dut/receiver/state /tb_image_link/dut/receiver/unique_packets
}
run all
quit
