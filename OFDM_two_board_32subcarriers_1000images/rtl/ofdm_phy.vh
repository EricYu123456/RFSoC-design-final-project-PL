// Shared over-the-air parameters. Include inside the modem and testbench modules.
localparam integer OFDM_SAMPLES = 64;
localparam integer OFDM_CP = 16;
localparam integer OFDM_SAMPLE_CLOCKS = 8;
localparam integer OFDM_MARKER = 24;
localparam integer OFDM_SYMBOL_CLOCKS = (OFDM_SAMPLES + OFDM_CP) * OFDM_SAMPLE_CLOCKS;
localparam integer OFDM_BYTES = 8;
localparam integer OFDM_DATA_PER_TRAIN = 2;

function active_bin;
    input [5:0] bin;
    begin active_bin = (bin >= 1 && bin <= 16) || bin >= 48; end
endfunction

// Active bins are sent in order +1..+16, then -16..-1.
function [4:0] tone_number;
    input [5:0] bin;
    begin tone_number = bin >= 48 ? bin - 32 : bin - 1; end
endfunction

function training_negative;
    input [4:0] tone;
    // Fixed BPSK sequence spreads training energy across the symbol.
    localparam [31:0] TRAIN_SIGNS = 32'h69b8462d;
    begin training_negative = TRAIN_SIGNS[tone]; end
endfunction
