# Run after link_design (impl opt_design PRE hook), when the scoped IP clocks
# exist. This is Tcl rather than XDC because collection guards use control flow.
# RFDC owns the primary clock. Derive the MMCM output from that clock instead
# of the redundant primary clock created by Clocking Wizard at its input.
set rf_mmcm_input [get_pins -quiet {rf_system_i/rf_clock/inst/mmcme4_adv_inst/CLKIN1}]
if {[llength $rf_mmcm_input] != 1} {error "RF MMCM input pin was not found"}
foreach rf_input_clock [get_clocks -quiet -of_objects $rf_mmcm_input] {
    if {![get_property IS_GENERATED $rf_input_clock] && [get_property NAME $rf_input_clock] ne "RFADC2_CLK"} {
        set rf_clock_name [get_property NAME $rf_input_clock]
        set rf_clock_target [get_property SOURCE_PINS $rf_input_clock]
        set rf_clock_root [get_property SOURCE_PINS [get_clocks RFADC2_CLK]]
        puts "RF_CLOCK_REPLACE=$rf_clock_name target=$rf_clock_target source=$rf_clock_root"
        create_generated_clock -name $rf_clock_name -source [get_pins $rf_clock_root] \
            -divide_by 1 [get_pins $rf_clock_target]
    }
}
set rf_primary [get_clocks -quiet RFADC2_CLK]
if {[llength $rf_primary] != 1} {error "RFDC primary clock RFADC2_CLK was not found"}
# Retain the original conservative 100 ps clock-input jitter budget.
set_input_jitter $rf_primary 0.100
