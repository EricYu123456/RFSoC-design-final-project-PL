"""Exercise the shipping Python send/receive/verify methods with two in-memory devices."""
import json
from pathlib import Path
from queue import Empty, Queue
import sys
from tempfile import TemporaryDirectory
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "board"))
import run_on_board as app


class Memory:
    def __init__(self):
        self.buffer = bytearray(524288)
    def write(self, address, data):
        self.buffer[address:address+len(data)] = data
    def read(self, address):
        return int.from_bytes(self.buffer[address:address+4],"little")


class Endpoint(app.PynqImageLinkIO):
    def __init__(self, sender, link):
        self.sender, self.link = sender, link
        self.source, self.sink = Memory(), Memory()
        self.control = 0
        self.done = False
    def apply_control(self, word):
        if word & app.RESET:
            self.done = False
        elif word & app.START and not self.control & app.START:
            if self.sender:
                self.done = False
                self.link[0].put(bytes(self.source.buffer))
            else:
                self.done = False
                self.link[1].put(True)
        self.control = word
    def read_status(self):
        try:
            if self.sender:
                self.link[1].get_nowait()
            else:
                data = self.link[0].get_nowait()
                self.sink.write(0,data[:app.FRAME_BYTES])
                self.sink.write(app.FRAME_BYTES,bytes([2])*300)
                self.sink.write(0x7fff0,data[0x7fff0:0x7fff8])
            self.done = True
        except Empty:
            pass
        return app.MODEM_STATUS_ID<<25 | 4 | (9 if self.done and not self.sender else int(self.done))
    def read_packet_counter(self):
        return 0
    def adc_overrange(self):
        return False
    def read_levels(self):
        return 8192<<16


def main():
    with TemporaryDirectory() as temp:
        root=Path(temp)
        inputs=[]
        for i in range(2):
            p=root/f"source{i}.raw"
            p.write_bytes(bytes([63+i])*app.FRAME_BYTES)
            inputs.append(p)
        link=(Queue(),Queue())
        boards=[]
        for role in ("send","receive"):
            board=app.BoardRun(role,root/"unused",root/role,image=inputs)
            board.link=Endpoint(role=="send",link)
            board.phase="pilot_calibration"
            # Temporary fixture shaped like the two automatically exported records.
            board.result.update(physical_board_tested=True,bit_sha256="fixture",hwh_sha256="fixture")
            boards.append(board)
        with ThreadPoolExecutor(max_workers=2) as pool:
            b=pool.submit(boards[1].receive)
            boards[0].send()
            b.result()
        assert app.verify(root/"send",root/"receive")["passed"]
        rx=root/"receive"
        record=json.loads((rx/"result.json").read_text())
        sub=rx/f"{record['session']:08x}"/"0001"
        damaged=bytearray((sub/"received.raw").read_bytes()); damaged[0]^=1
        (sub/"received.raw").write_bytes(damaged)
        record["frames"][1]["sha256"]=app.digest(damaged)
        (rx/"result.json").write_text(json.dumps(record))
        result=app.verify(root/"send",rx)
        assert not result["passed"] and result["frames"][1]["mismatched_bytes"]==1
    print("PASS host batch runner, frame metadata and offline byte comparison")


if __name__=="__main__":
    main()

