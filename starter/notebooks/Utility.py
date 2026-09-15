import hashlib
import io
import zipfile
import yaml
from quantum_lake_student.formats import iter_b8_records, parse_01_records


class Utility:

    def __init__(self):
        pass

    @staticmethod
    def say_hello(name: str = "world") -> str:
        return f"Hello, {name}!"


    @staticmethod
    def list_zip_folders(raw_bytes: bytes) -> list[str]:
        with zipfile.ZipFile(io.BytesIO(raw_bytes)) as z:
            names = z.namelist()
        return sorted({n.split("/")[0] for n in names if "/" in n}) # set of folder name (gets all unique values!)


    @staticmethod
    def read_zip_member_bytes(raw_bytes: bytes, member_name: str) -> bytes:
        with zipfile.ZipFile(io.BytesIO(raw_bytes)) as z:
            if member_name in z.namelist():
                return z.read(member_name)
        return b""

    @staticmethod
    def read_zip_member_yaml(raw_bytes: bytes, member_name: str) -> dict:
        raw = Utility.read_zip_member_bytes(raw_bytes, member_name)
        return yaml.safe_load(raw) if raw else {}

    @staticmethod
    def read_b8_records(
        raw_bytes: bytes, member_name: str, bits_per_record: int
    ) -> list[tuple[int, ...]]:
        raw = Utility.read_zip_member_bytes(raw_bytes, member_name)
        if not raw or bits_per_record == 0:
            return []
        return list(iter_b8_records(raw, bits_per_record=bits_per_record))

    @staticmethod
    def read_01_records(raw_bytes: bytes, member_name: str) -> list[int]:
        raw = Utility.read_zip_member_bytes(raw_bytes, member_name)
        if not raw:
            return []
        return parse_01_records(raw)

    @staticmethod
    def check_equal_lengths(**named_lists) -> bool:

        active_lists = {
            name: lst for name, lst in named_lists.items() if len(lst) > 0
        } # we removed empty lists
        lengths = {name: len(lst) for name, lst in active_lists.items()}
        unique_lengths = set(lengths.values())
        if len(unique_lengths) == 1:
            print(
                f"OK: all {len(active_lists)} active lists have length"
                f" {unique_lengths.pop()}"
            )
            return True
        print("MISMATCH in lengths:")
        for name, length in lengths.items():
            print(f"  {name}: {length}")
        return False

    @staticmethod
    def count_set_bits(bit_tuple: tuple[int, ...]) -> int:
        return sum(bit_tuple)

    @staticmethod
    def pack_bits_to_bytes(bit_tuple: tuple[int, ...]) -> bytes:
        num_bytes = (len(bit_tuple) + 7) // 8
        out = bytearray(num_bytes)
        for bit_index, bit in enumerate(bit_tuple):
            if bit:
                out[bit_index // 8] |= 1 << (bit_index % 8)
        return bytes(out)


    @staticmethod
    def build_shot_rows(raw_bytes: bytes, folder: str) -> list[dict]:
        properties = Utility.read_zip_member_yaml(
            raw_bytes, f"{folder}/properties.yml"
        )

        measurements = Utility.read_b8_records(
            raw_bytes,
            f"{folder}/measurements.b8",
            properties.get("circuit_measurements", 0),
        )
        detections = Utility.read_b8_records(
            raw_bytes,
            f"{folder}/detection_events.b8",
            properties.get("circuit_detectors", 0),
        )
        sweeps = Utility.read_b8_records(
            raw_bytes,
            f"{folder}/sweep.b8",
            properties.get("circuit_sweep_bits", 0),
        )

        actual_flips = Utility.read_01_records(
            raw_bytes, f"{folder}/obs_flips_actual.01"
        )
        belief = Utility.read_01_records(
            raw_bytes,
            f"{folder}/obs_flips_predicted_by_belief_matching.01",
        )
        correlated = Utility.read_01_records(
            raw_bytes,
            f"{folder}/obs_flips_predicted_by_correlated_matching.01",
        )
        pymatching = Utility.read_01_records(
            raw_bytes, f"{folder}/obs_flips_predicted_by_pymatching.01"
        )
        tensor_net = Utility.read_01_records(
            raw_bytes,
            f"{folder}/obs_flips_predicted_by_tensor_network_contraction.01",
        )

        ok = Utility.check_equal_lengths(
            measurements=measurements,
            detections=detections,
            sweeps=sweeps,
            actual_flips=actual_flips,
            belief=belief,
            correlated=correlated,
            pymatching=pymatching,
            tensor_net=tensor_net,
        )
        if not ok:
            raise ValueError(f"Companion file length mismatch in {folder}")

        exp_id = f"google_{folder}"

        rows = []
        for shot_index in range(len(actual_flips)):
            shot_rec_id = f"google_shot_{hashlib.md5(f'{exp_id}_{shot_index}'.encode()).hexdigest()[:12]}"

            rows.append(
                {
                    "source_record_id": shot_rec_id,
                    "experiment_id": exp_id,
                    "shot_index": shot_index,
                    "measurement_bits": Utility.pack_bits_to_bytes(
                        measurements[shot_index]
                    ),
                    "sweep_bits": (
                        Utility.pack_bits_to_bytes(sweeps[shot_index])
                        if sweeps
                        else b""
                    ),
                    "detector_bits": Utility.pack_bits_to_bytes(
                        detections[shot_index]
                    ),
                    "detector_event_count": Utility.count_set_bits(
                        detections[shot_index]
                    ),
                    "actual_observable_flip": bool(actual_flips[shot_index]),
                    "belief_matching_prediction": (
                        bool(belief[shot_index]) if belief else False
                    ),
                    "correlated_matching_prediction": (
                        bool(correlated[shot_index]) if correlated else False
                    ),
                    "pymatching_prediction": (
                        bool(pymatching[shot_index]) if pymatching else False
                    ),
                    "tensor_network_contraction_prediction": (
                        bool(tensor_net[shot_index]) if tensor_net else False
                    ),
                }
            )
        return rows