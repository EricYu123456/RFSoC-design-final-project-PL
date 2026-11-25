# 
# WRITE CDC Waivers
# cmd: write_waivers -force X:/reports/implementation_qam16/cdc_waivers.xdc
current_instance -quiet
create_waiver -type CDC -id {CDC-11} -user "OFDM_RF" -desc "Independent ADC/DAC clock-valid status flags, each with RFDC four-stage synchronization; no coherent reconvergence is required." -from [get_pins -quiet {rf_system_i/rf_reset/U0/ACTIVE_LOW_PR_OUT_DFF[0].FDRE_PER_N/C}] -to [get_pins -quiet {rf_system_i/rfdc/inst/cdc_adc2_clk_valid_i/syncstages_ff_reg[0]/D}] -timestamp "Wed Nov 25 08:51:48 GMT 2026" ;#1
create_waiver -type CDC -id {CDC-11} -user "OFDM_RF" -desc "Independent ADC/DAC clock-valid status flags, each with RFDC four-stage synchronization; no coherent reconvergence is required." -from [get_pins -quiet {rf_system_i/rf_reset/U0/ACTIVE_LOW_PR_OUT_DFF[0].FDRE_PER_N/C}] -to [get_pins -quiet {rf_system_i/rfdc/inst/cdc_dac2_clk_valid_i/syncstages_ff_reg[0]/D}] -timestamp "Wed Nov 25 08:51:48 GMT 2026" ;#2
# 
current_instance -quiet
