# Board LEDs are asynchronous human indicators, with no external clocked load.
# These are the only GPIO timing exceptions. RFDC pins/clocks are constrained
# by the RF Data Converter IP; all PS/PL data paths retain setup/hold analysis.
set_property PACKAGE_PIN AR11 [get_ports {leds[0]}]
set_property PACKAGE_PIN AW10 [get_ports {leds[1]}]
set_property PACKAGE_PIN AT11 [get_ports {leds[2]}]
set_property PACKAGE_PIN AU10 [get_ports {leds[3]}]
set_property IOSTANDARD LVCMOS18 [get_ports {leds[*]}]
set_false_path -to [get_ports {leds[*]}]
