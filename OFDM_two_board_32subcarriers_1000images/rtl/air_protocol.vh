// All multibyte fields are big-endian on air. Both directions use CRC-32/ISO-HDLC.
localparam [7:0] DATA=0, OPEN=1, READY=2, POLL=3, SACK=4, FIN=5, CLOSED=6;
localparam integer HEADER_BYTES=11, CONTROL_BYTES=15, DATA_BYTES=1043;

