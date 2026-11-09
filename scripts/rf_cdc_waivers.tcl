# RFDC synchronizes fabric-reset/clock-valid into its AXI status domain twice,
# once for ADC status and once for DAC status. These are independent status
# flags, not reconvergent data or a multi-bit control. Each has a vendor four-
# stage synchronizer; coherent assertion between the two flags is not required.
# Waive ONLY these two reset-status fanout paths; retain all timing constraints.
set cdc_reset_source [get_pins -quiet {rf_system_i/rf_reset/U0/ACTIVE_LOW_PR_OUT_DFF[0].FDRE_PER_N/C}]
if {[llength $cdc_reset_source] != 1} {error "Expected RF reset launch flop not found"}
foreach endpoint {
    rf_system_i/rfdc/inst/cdc_adc2_clk_valid_i/syncstages_ff_reg[0]/D
    rf_system_i/rfdc/inst/cdc_dac2_clk_valid_i/syncstages_ff_reg[0]/D
} {
    set cdc_destination [get_pins -quiet $endpoint]
    if {[llength $cdc_destination] != 1} {error "Expected RFDC status synchronizer not found: $endpoint"}
    create_waiver -type CDC -id CDC-11 -from $cdc_reset_source -to $cdc_destination \
        -user OOK_RF -description {Independent ADC/DAC clock-valid status flags, each with RFDC four-stage synchronization; no coherent reconvergence is required.}
}
