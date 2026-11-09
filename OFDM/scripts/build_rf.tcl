# Reproducible RFSoC 4x2 / PYNQ hardware build. No DRC severity overrides.
set root [file normalize [file join [file dirname [info script]] ..]]
cd $root
set build_dir [file join $root build rf]
set report_dir [file join $root reports implementation_rf]
set out_dir [file join $build_dir artifacts]
if {[version -short] ne "2024.2"} {error "This overlay requires Vivado 2024.2"}
if {[info exists ::env(OFDM_BUILD_PYTHON)]} {
    set python $::env(OFDM_BUILD_PYTHON)
} else {
    set python python
}
# Vivado sets PYTHONHOME for its bundled interpreter. Isolate the external Python.
set gate [list $python -I [file join $root scripts build_rf.py]]
# The direct Tcl entry point checks the same fresh, full simulation as the CLI.
set fingerprint [exec {*}$gate --check-simulation]
file delete -force [file join $build_dir release_receipt.json]
file mkdir $build_dir $report_dir $out_dir [file join $build_dir ip]
set_param general.maxThreads 4
create_project -force ofdm_rf [file join $build_dir vivado] -part xczu48dr-ffvg1517-2-e
set_property board_part realdigital.org:rfsoc4x2:part0:1.0 [current_project]
set_property target_language Verilog [current_project]
set_property XPM_LIBRARIES XPM_CDC [current_project]
set include_dirs [list [file join $root rtl] [file join $root board rtl]]
set_property include_dirs $include_dirs [get_filesets sources_1]
foreach dir $include_dirs {
    add_files [lsort [glob -directory $dir -types f -nocomplain *.v *.sv *.vh *.svh]]
}
add_files -fileset constrs_1 [list [file join $root constraints rfsoc4x2_rf.xdc]]

create_ip -name blk_mem_gen -vendor xilinx.com -library ip -version 8.4 \
    -module_name ip_packet_ram -dir [file join $build_dir ip]
set_property -dict [list CONFIG.Memory_Type Simple_Dual_Port_RAM \
    CONFIG.Write_Width_A 8 CONFIG.Write_Depth_A 1024 CONFIG.Read_Width_B 8 \
    CONFIG.Enable_A Use_ENA_Pin CONFIG.Enable_B Use_ENB_Pin \
    CONFIG.Register_PortB_Output_of_Memory_Primitives false \
    CONFIG.Register_PortB_Output_of_Memory_Core false \
    CONFIG.Assume_Synchronous_Clk true CONFIG.Use_RSTA_Pin false CONFIG.Use_RSTB_Pin false \
    CONFIG.Port_A_Clock 153.6 CONFIG.Port_B_Clock 153.6] [get_ips ip_packet_ram]
generate_target all [get_ips ip_packet_ram]

create_bd_design rf_system
create_bd_cell -type ip -vlnv xilinx.com:ip:zynq_ultra_ps_e:3.5 ps
apply_bd_automation -rule xilinx.com:bd_rule:zynq_ultra_ps_e -config {apply_board_preset "1"} [get_bd_cells ps]
set_property -dict [list CONFIG.PSU__USE__M_AXI_GP0 1 CONFIG.PSU__USE__M_AXI_GP1 0 \
    CONFIG.PSU__USE__M_AXI_GP2 0 CONFIG.PSU__USE__IRQ0 0 CONFIG.PSU__USE__IRQ1 0 \
    CONFIG.PSU__GPIO_EMIO__PERIPHERAL__ENABLE 0 CONFIG.PSU__FPGA_PL0_ENABLE 1 \
    CONFIG.PSU__FPGA_PL1_ENABLE 0 CONFIG.PSU__FPGA_PL2_ENABLE 0 CONFIG.PSU__FPGA_PL3_ENABLE 0 \
    CONFIG.PSU__CRL_APB__PL0_REF_CTRL__FREQMHZ 100 \
    CONFIG.PSU__DDRC__ROW_ADDR_COUNT 16] [get_bd_cells ps]
create_bd_cell -type ip -vlnv xilinx.com:ip:usp_rf_data_converter:2.6 rfdc
# DAC_A (tile 230, block 0) -> ADC_A (tile 226, dual block 2).
# Fs=4915.2 MHz; 16x interpolation/decimation; 2 complex samples/153.6 MHz beat.
set_property -dict [list CONFIG.ADC_Slice00_Enable false CONFIG.ADC_Slice02_Enable false \
    CONFIG.ADC0_Enable 0 CONFIG.ADC1_Enable 0 CONFIG.ADC2_Enable 1 CONFIG.ADC3_Enable 0 \
    CONFIG.DAC0_Enable 0 CONFIG.DAC1_Enable 0 CONFIG.DAC2_Enable 1 CONFIG.DAC3_Enable 0 \
    CONFIG.ADC2_PLL_Enable true CONFIG.ADC2_Refclk_Freq 491.520 CONFIG.ADC2_Sampling_Rate 4.9152 \
    CONFIG.ADC2_Outclk_Freq 307.200 CONFIG.ADC2_Multi_Tile_Sync false \
    CONFIG.ADC_Slice20_Enable false CONFIG.ADC_Slice22_Enable true \
    CONFIG.ADC_Data_Type22 1 CONFIG.ADC_Data_Width22 2 CONFIG.ADC_Decimation_Mode22 16 \
    CONFIG.ADC_Mixer_Type22 2 CONFIG.ADC_NCO_Freq22 -0.5 \
    CONFIG.DAC2_PLL_Enable true CONFIG.DAC2_Refclk_Freq 491.520 CONFIG.DAC2_Sampling_Rate 4.9152 \
    CONFIG.DAC2_Outclk_Freq 307.200 CONFIG.DAC2_Multi_Tile_Sync false \
    CONFIG.DAC_Slice20_Enable true CONFIG.DAC_Slice21_Enable false \
    CONFIG.DAC_Data_Type20 0 CONFIG.DAC_Data_Width20 4 CONFIG.DAC_Interpolation_Mode20 16 \
    CONFIG.DAC_Mixer_Type20 2 CONFIG.DAC_NCO_Freq20 0.5] [get_bd_cells rfdc]
foreach intf {adc2_clk dac2_clk vin2_23 vout20} {
    make_bd_intf_pins_external [get_bd_intf_pins rfdc/$intf]
}
foreach intf {adc2_clk_0 dac2_clk_0} {
    set_property CONFIG.FREQ_HZ 491520000 [get_bd_intf_ports $intf]
}
# SYSREF is not needed for this single-channel, non-MTS design. RFDC exposes it
# as a dedicated differential input; leave the board's clock source connected.
if {[llength [get_bd_intf_pins -quiet rfdc/sysref_in]]} {
    make_bd_intf_pins_external [get_bd_intf_pins rfdc/sysref_in]
}
create_bd_cell -type ip -vlnv xilinx.com:ip:clk_wiz:6.0 rf_clock
set_property -dict [list CONFIG.PRIM_IN_FREQ 307.200 CONFIG.CLKOUT1_REQUESTED_OUT_FREQ 153.600 \
    CONFIG.USE_RESET false CONFIG.USE_LOCKED true CONFIG.CLKIN1_JITTER_PS 100.0] [get_bd_cells rf_clock]
connect_bd_net [get_bd_pins rfdc/clk_adc2] [get_bd_pins rf_clock/clk_in1]

create_bd_cell -type ip -vlnv xilinx.com:ip:xlconstant:1.1 one
set_property CONFIG.CONST_VAL 1 [get_bd_cells one]
create_bd_cell -type ip -vlnv xilinx.com:ip:xlconstant:1.1 zero
set_property CONFIG.CONST_VAL 0 [get_bd_cells zero]
foreach name {ps_reset rf_reset} {
    create_bd_cell -type ip -vlnv xilinx.com:ip:proc_sys_reset:5.0 $name
    connect_bd_net [get_bd_pins ps/pl_resetn0] [get_bd_pins $name/ext_reset_in]
    # Auxiliary reset is active low, while the debug reset is active high.
    connect_bd_net [get_bd_pins one/dout] [get_bd_pins $name/aux_reset_in]
    connect_bd_net [get_bd_pins zero/dout] [get_bd_pins $name/mb_debug_sys_rst]
}
connect_bd_net [get_bd_pins ps/pl_clk0] [get_bd_pins ps_reset/slowest_sync_clk]
connect_bd_net [get_bd_pins one/dout] [get_bd_pins ps_reset/dcm_locked]
connect_bd_net [get_bd_pins rf_clock/clk_out1] [get_bd_pins rf_reset/slowest_sync_clk]
connect_bd_net [get_bd_pins rf_clock/locked] [get_bd_pins rf_reset/dcm_locked]

create_bd_cell -type module -reference ofdm_rf_board modem
create_bd_cell -type module -reference ofdm_rf_host host
create_bd_cell -type ip -vlnv xilinx.com:ip:smartconnect:1.0 axi
set_property -dict [list CONFIG.NUM_SI 1 CONFIG.NUM_MI 6 CONFIG.NUM_CLKS 1] [get_bd_cells axi]
connect_bd_intf_net [get_bd_intf_pins ps/M_AXI_HPM0_FPD] [get_bd_intf_pins axi/S00_AXI]
connect_bd_net [get_bd_pins ps/pl_clk0] [get_bd_pins axi/aclk] [get_bd_pins ps/maxihpm0_fpd_aclk] \
    [get_bd_pins rfdc/s_axi_aclk] [get_bd_pins host/ps_clk]
connect_bd_net [get_bd_pins rf_clock/clk_out1] [get_bd_pins modem/clk] [get_bd_pins host/rf_clk] \
    [get_bd_pins rfdc/m2_axis_aclk] [get_bd_pins rfdc/s2_axis_aclk]
connect_bd_net [get_bd_pins ps_reset/peripheral_aresetn] [get_bd_pins axi/aresetn] \
    [get_bd_pins rfdc/s_axi_aresetn] [get_bd_pins host/ps_aresetn]
connect_bd_net [get_bd_pins rf_reset/peripheral_aresetn] [get_bd_pins modem/aresetn] [get_bd_pins host/rf_aresetn] \
    [get_bd_pins rfdc/m2_axis_aresetn] [get_bd_pins rfdc/s2_axis_aresetn]
connect_bd_intf_net [get_bd_intf_pins axi/M00_AXI] [get_bd_intf_pins rfdc/s_axi]
connect_bd_intf_net [get_bd_intf_pins rfdc/m22_axis] [get_bd_intf_pins modem/ADC_I]
connect_bd_intf_net [get_bd_intf_pins rfdc/m23_axis] [get_bd_intf_pins modem/ADC_Q]
connect_bd_intf_net [get_bd_intf_pins modem/DAC] [get_bd_intf_pins rfdc/s20_axis]
create_bd_port -dir O -from 3 -to 0 leds
connect_bd_net [get_bd_pins modem/leds] [get_bd_ports leds]

foreach {name interface master_index} {source SOURCE 1 sink SINK 2} {
    create_bd_cell -type ip -vlnv xilinx.com:ip:axi_bram_ctrl:4.1 ${name}_ctrl
    set_property -dict [list CONFIG.DATA_WIDTH 32 CONFIG.SINGLE_PORT_BRAM 1 CONFIG.ECC_TYPE 0] [get_bd_cells ${name}_ctrl]
    create_bd_cell -type ip -vlnv xilinx.com:ip:blk_mem_gen:8.4 ${name}_ram
    set_property -dict [list CONFIG.Memory_Type True_Dual_Port_RAM CONFIG.use_bram_block BRAM_Controller \
        CONFIG.Enable_32bit_Address true CONFIG.Write_Width_A 32 CONFIG.Write_Width_B 32 \
        CONFIG.Read_Width_A 32 CONFIG.Read_Width_B 32 CONFIG.Write_Depth_A 131072 \
        CONFIG.Use_Byte_Write_Enable true CONFIG.Byte_Size 8 CONFIG.Assume_Synchronous_Clk false \
        CONFIG.Port_A_Clock 100 CONFIG.Port_B_Clock 153.6 \
        CONFIG.Enable_A Use_ENA_Pin CONFIG.Enable_B Use_ENB_Pin \
        CONFIG.Register_PortA_Output_of_Memory_Primitives false CONFIG.Register_PortB_Output_of_Memory_Primitives false \
        CONFIG.Register_PortA_Output_of_Memory_Core false CONFIG.Register_PortB_Output_of_Memory_Core false \
        CONFIG.Use_RSTA_Pin true CONFIG.Use_RSTB_Pin true CONFIG.EN_SAFETY_CKT false] [get_bd_cells ${name}_ram]
    connect_bd_intf_net [get_bd_intf_pins ${name}_ctrl/BRAM_PORTA] [get_bd_intf_pins ${name}_ram/BRAM_PORTA]
    connect_bd_intf_net [get_bd_intf_pins modem/$interface] [get_bd_intf_pins ${name}_ram/BRAM_PORTB]
    connect_bd_intf_net [get_bd_intf_pins axi/M0${master_index}_AXI] [get_bd_intf_pins ${name}_ctrl/S_AXI]
    connect_bd_net [get_bd_pins ps/pl_clk0] [get_bd_pins ${name}_ctrl/s_axi_aclk]
    connect_bd_net [get_bd_pins ps_reset/peripheral_aresetn] [get_bd_pins ${name}_ctrl/s_axi_aresetn]
}
foreach {name master_index} {control 3 telemetry 4 host_state 5} {
    create_bd_cell -type ip -vlnv xilinx.com:ip:axi_gpio:2.0 $name
    set_property -dict [list CONFIG.C_IS_DUAL 1 CONFIG.C_GPIO_WIDTH 32 CONFIG.C_GPIO2_WIDTH 32 \
        CONFIG.C_ALL_INPUTS_2 1] [get_bd_cells $name]
    connect_bd_intf_net [get_bd_intf_pins axi/M0${master_index}_AXI] [get_bd_intf_pins $name/S_AXI]
    connect_bd_net [get_bd_pins ps/pl_clk0] [get_bd_pins $name/s_axi_aclk]
    connect_bd_net [get_bd_pins ps_reset/peripheral_aresetn] [get_bd_pins $name/s_axi_aresetn]
}
set_property -dict [list CONFIG.C_ALL_OUTPUTS 1 CONFIG.C_DOUT_DEFAULT 0x04004001] [get_bd_cells control]
set_property CONFIG.C_ALL_INPUTS 1 [get_bd_cells telemetry]
set_property CONFIG.C_ALL_INPUTS 1 [get_bd_cells host_state]
foreach signal {control status levels retries} {
    connect_bd_net [get_bd_pins modem/$signal] [get_bd_pins host/rf_$signal]
}
connect_bd_net [get_bd_pins control/gpio_io_o] [get_bd_pins host/ps_control]
connect_bd_net [get_bd_pins control/gpio2_io_i] [get_bd_pins host/ps_status]
connect_bd_net [get_bd_pins telemetry/gpio_io_i] [get_bd_pins host/ps_levels]
connect_bd_net [get_bd_pins telemetry/gpio2_io_i] [get_bd_pins host/ps_retries]
connect_bd_net [get_bd_pins host_state/gpio_io_i] [get_bd_pins host/ps_health]
connect_bd_net [get_bd_pins host_state/gpio2_io_i] [get_bd_pins host/ps_applied_control]

assign_bd_address -offset 0xA0000000 -range 512K -target_address_space [get_bd_addr_spaces ps/Data] [get_bd_addr_segs source_ctrl/S_AXI/Mem0]
assign_bd_address -offset 0xA0080000 -range 512K -target_address_space [get_bd_addr_spaces ps/Data] [get_bd_addr_segs sink_ctrl/S_AXI/Mem0]
assign_bd_address -offset 0xA0100000 -range 64K -target_address_space [get_bd_addr_spaces ps/Data] [get_bd_addr_segs control/S_AXI/Reg]
assign_bd_address -offset 0xA0110000 -range 64K -target_address_space [get_bd_addr_spaces ps/Data] [get_bd_addr_segs telemetry/S_AXI/Reg]
assign_bd_address -offset 0xA0120000 -range 64K -target_address_space [get_bd_addr_spaces ps/Data] [get_bd_addr_segs host_state/S_AXI/Reg]
assign_bd_address -offset 0xA0140000 -range 256K -target_address_space [get_bd_addr_spaces ps/Data] [get_bd_addr_segs rfdc/s_axi/Reg]
validate_bd_design
foreach name {ps_reset rf_reset} {
    foreach polarity {C_EXT_RESET_HIGH C_AUX_RESET_HIGH} {
        if {[get_property CONFIG.$polarity [get_bd_cells $name]] != 0} {
            error "Unexpected reset polarity $name/$polarity"
        }
    }
}
save_bd_design
generate_target all [get_files rf_system.bd]
make_wrapper -files [get_files rf_system.bd] -top
add_files [list [file join $build_dir vivado ofdm_rf.gen sources_1 bd rf_system hdl rf_system_wrapper.v]]
set_property top rf_system_wrapper [get_filesets sources_1]
update_compile_order -fileset sources_1
write_bd_tcl -force [file join $report_dir rf_system.tcl]
set_property STEPS.PHYS_OPT_DESIGN.IS_ENABLED true [get_runs impl_1]
set_property STEPS.PHYS_OPT_DESIGN.ARGS.DIRECTIVE AggressiveExplore [get_runs impl_1]
set_property STEPS.ROUTE_DESIGN.ARGS.DIRECTIVE Explore [get_runs impl_1]
add_files -fileset utils_1 [list [file join $root scripts rf_clock_constraints.tcl]]
set_property STEPS.OPT_DESIGN.TCL.PRE [file join $root scripts rf_clock_constraints.tcl] [get_runs impl_1]
if {[exec {*}$gate --check-simulation] ne $fingerprint} {error "OFDM build inputs changed before synthesis"}
launch_runs impl_1 -to_step route_design -jobs 4
wait_on_run impl_1
if {[get_property PROGRESS [get_runs impl_1]] ne "100%"} {error "Implementation failed; inspect runme.log"}
open_run impl_1
source [file join $root scripts verify_rf_timing.tcl]
if {[exec {*}$gate --check-simulation] ne $fingerprint} {error "OFDM build inputs changed during implementation"}
write_checkpoint -force [file join $build_dir ofdm_rf_routed.dcp]
write_bitstream -force [file join $out_dir ofdm_rf.bit]
# Hardware description is exported separately; PYNQ uses the adjacent .bit/.hwh.
file mkdir [file join $out_dir hardware_description]
write_hw_platform -fixed -force -file [file join $out_dir hardware_description ofdm_rf.xsa]
file copy -force [file join $build_dir vivado ofdm_rf.gen sources_1 bd rf_system hw_handoff rf_system.hwh] [file join $out_dir ofdm_rf.hwh]
exec {*}$gate --record-build $fingerprint
puts "RF_BUILD_PASS=$out_dir"
exit
