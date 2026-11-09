set root [file normalize [file join [file dirname [info script]] ..]]
cd $root
set part_name xczu48dr-ffvg1517-2-e
file mkdir build/ip
create_project -force ook_image_link build/vivado -part $part_name
set_property target_language Verilog [current_project]
set_property simulator_language Mixed [current_project]
set_property target_simulator XSim [current_project]
set_property include_dirs [list [file join $root rtl]] [get_filesets sources_1]
add_files [glob [file join $root rtl *.v]]
add_files -fileset constrs_1 [file join $root constraints simulation_target.xdc]
set_property top image_link_synth [get_filesets sources_1]

proc ensure_ip {name version module} {
    set xci [file normalize [file join build ip $module ${module}.xci]]
    if {[file exists $xci]} {read_ip $xci} else {
        create_ip -name $name -vendor xilinx.com -library ip -version $version -module_name $module -dir build/ip
    }
}
ensure_ip dds_compiler 6.0 ip_carrier
set_property -dict [list CONFIG.Parameter_Entry {Hardware_Parameters} \
    CONFIG.PartsPresent {Phase_Generator_and_SIN_COS_LUT} \
    CONFIG.Phase_Width {16} CONFIG.Output_Width {16} \
    CONFIG.Output_Selection {Sine} CONFIG.Noise_Shaping {None} \
    CONFIG.Phase_Increment {Fixed} CONFIG.PINC1 {0010000000000000} \
    CONFIG.Phase_offset {None} CONFIG.Has_Phase_Out {false} \
    CONFIG.Amplitude_Mode {Unit_Circle} CONFIG.Has_ARESETn {true} \
    CONFIG.Has_TREADY {false} CONFIG.DDS_Clock_Rate {100}] [get_ips ip_carrier]

ensure_ip mult_gen 12.0 ip_square
set_property -dict [list CONFIG.PortAWidth {16} CONFIG.PortBWidth {16} \
    CONFIG.PortAType {Signed} CONFIG.PortBType {Signed} \
    CONFIG.Multiplier_Construction {Use_Mults} CONFIG.PipeStages {1} \
    CONFIG.SyncClear {true} CONFIG.Use_Custom_Output_Width {false}] [get_ips ip_square]

ensure_ip blk_mem_gen 8.4 ip_packet_ram
set_property -dict [list CONFIG.Memory_Type {Simple_Dual_Port_RAM} \
    CONFIG.Write_Width_A {8} CONFIG.Write_Depth_A {1024} \
    CONFIG.Read_Width_A {8} CONFIG.Read_Width_B {8} \
    CONFIG.Enable_A {Use_ENA_Pin} CONFIG.Enable_B {Use_ENB_Pin} \
    CONFIG.Register_PortA_Output_of_Memory_Primitives {false} \
    CONFIG.Register_PortB_Output_of_Memory_Primitives {false} \
    CONFIG.Register_PortB_Output_of_Memory_Core {false} \
    CONFIG.Assume_Synchronous_Clk {true} CONFIG.Use_RSTA_Pin {false} \
    CONFIG.Use_RSTB_Pin {false}] [get_ips ip_packet_ram]

generate_target all [get_ips]
add_files -fileset sim_1 [file join $root sim tb_image_link.sv]
set_property include_dirs [list [file join $root rtl]] [get_filesets sim_1]
set_property top tb_image_link [get_filesets sim_1]
set_property xsim.simulate.runtime all [get_filesets sim_1]
set gui_output [file join $root reports gui_baseline]
file mkdir $gui_output
set_property -name xsim.simulate.xsim.more_options -value [format {-testplusarg "ROOT=%s" -testplusarg "OUT=%s" -testplusarg "CASE=baseline" -testplusarg "PACKETS=3"} $root $gui_output] -objects [get_filesets sim_1]
update_compile_order -fileset sources_1
update_compile_order -fileset sim_1
launch_simulation -scripts_only
puts "PROJECT_READY=[file join $root build vivado ook_image_link.xpr]"
exit
