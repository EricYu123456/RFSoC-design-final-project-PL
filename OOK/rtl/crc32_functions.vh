// CRC-32/ISO-HDLC, reflected byte update. Final value is ~state.
function [31:0] crc32_update;
    input [31:0] state;
    input [7:0] data;
    integer bit_number;
    reg [31:0] value;
    begin
        value = state ^ {24'b0, data};
        for (bit_number = 0; bit_number < 8; bit_number = bit_number + 1)
            value = value[0] ? ((value >> 1) ^ 32'hEDB88320) : (value >> 1);
        crc32_update = value;
    end
endfunction
