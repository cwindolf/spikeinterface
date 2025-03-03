import pytest
import numpy as np
from pathlib import Path
import shutil

from spikeinterface import create_sorting_analyzer, get_template_extremum_channel, generate_ground_truth_recording
from spikeinterface.core.job_tools import divide_recording_into_chunks

# from spikeinterface.sortingcomponents.peak_detection import detect_peaks
from spikeinterface.core.node_pipeline import (
    run_node_pipeline,
    PeakRetriever,
    SpikeRetriever,
    PipelineNode,
    ExtractDenseWaveforms,
    sorting_to_peaks,
    spike_peak_dtype,
)


class AmplitudeExtractionNode(PipelineNode):
    def __init__(self, recording, parents=None, return_output=True, param0=5.5):
        PipelineNode.__init__(self, recording, parents=parents, return_output=return_output)
        self.param0 = param0
        self._dtype = np.dtype([("abs_amplitude", recording.get_dtype())])

    def get_dtype(self):
        return self._dtype

    def compute(self, traces, peaks):
        amps = np.zeros(peaks.size, dtype=self._dtype)
        amps["abs_amplitude"] = np.abs(peaks["amplitude"])
        return amps

    def get_trace_margin(self):
        return 5


class WaveformDenoiser(PipelineNode):
    # waveform smoother
    def __init__(self, recording, return_output=True, parents=None):
        PipelineNode.__init__(self, recording, return_output=return_output, parents=parents)

    def get_dtype(self):
        return np.dtype("float32")

    def compute(self, traces, peaks, waveforms):
        kernel = np.array([0.1, 0.8, 0.1])
        denoised_waveforms = np.apply_along_axis(lambda m: np.convolve(m, kernel, mode="same"), axis=1, arr=waveforms)
        return denoised_waveforms


class WaveformsRootMeanSquare(PipelineNode):
    def __init__(self, recording, return_output=True, parents=None):
        PipelineNode.__init__(self, recording, return_output=return_output, parents=parents)

    def get_dtype(self):
        return np.dtype("float32")

    def compute(self, traces, peaks, waveforms):
        rms_by_channels = np.sum(waveforms**2, axis=1)
        return rms_by_channels


@pytest.fixture(scope="module")
def cache_folder_creation(tmp_path_factory):
    cache_folder = tmp_path_factory.mktemp("cache_folder")
    return cache_folder


def test_run_node_pipeline(cache_folder_creation):
    cache_folder = cache_folder_creation
    recording, sorting = generate_ground_truth_recording(num_channels=10, num_units=10, durations=[10.0])

    # job_kwargs = dict(chunk_duration="0.5s", n_jobs=2, progress_bar=False)
    job_kwargs = dict(chunk_duration="0.5s", n_jobs=1, progress_bar=False)

    spikes = sorting.to_spike_vector()

    # create peaks from spikes
    sorting_analyzer = create_sorting_analyzer(sorting, recording, format="memory")
    sorting_analyzer.compute(["random_spikes", "templates"], **job_kwargs)
    extremum_channel_inds = get_template_extremum_channel(sorting_analyzer, peak_sign="neg", outputs="index")

    peaks = sorting_to_peaks(sorting, extremum_channel_inds, spike_peak_dtype)
    # print(peaks.size)

    peak_retriever = PeakRetriever(recording, peaks)
    # this test when no spikes in last chunks
    peak_retriever_few = PeakRetriever(recording, peaks[: peaks.size // 2])

    # channel index is from template
    spike_retriever_T = SpikeRetriever(
        sorting, recording, channel_from_template=True, extremum_channel_inds=extremum_channel_inds
    )
    # channel index is per spike
    spike_retriever_S = SpikeRetriever(
        sorting,
        recording,
        channel_from_template=False,
        extremum_channel_inds=extremum_channel_inds,
        radius_um=50,
        peak_sign="neg",
    )

    # test with 3 differents first nodes
    for loop, peak_source in enumerate((peak_retriever, peak_retriever_few, spike_retriever_T, spike_retriever_S)):
        # one step only : squeeze output
        nodes = [
            peak_source,
            AmplitudeExtractionNode(recording, parents=[peak_source], param0=6.6),
        ]
        step_one = run_node_pipeline(recording, nodes, job_kwargs, squeeze_output=True)
        if loop == 0:
            assert np.allclose(np.abs(peaks["amplitude"]), step_one["abs_amplitude"])

        # 3 nodes two have outputs
        ms_before = 0.5
        ms_after = 1.0
        peak_retriever = PeakRetriever(recording, peaks)
        dense_waveforms = ExtractDenseWaveforms(
            recording, parents=[peak_source], ms_before=ms_before, ms_after=ms_after, return_output=False
        )
        waveform_denoiser = WaveformDenoiser(recording, parents=[peak_source, dense_waveforms], return_output=False)
        amplitue_extraction = AmplitudeExtractionNode(recording, parents=[peak_source], param0=6.6, return_output=True)
        waveforms_rms = WaveformsRootMeanSquare(recording, parents=[peak_source, dense_waveforms], return_output=True)
        denoised_waveforms_rms = WaveformsRootMeanSquare(
            recording, parents=[peak_source, waveform_denoiser], return_output=True
        )

        nodes = [
            peak_source,
            dense_waveforms,
            waveform_denoiser,
            amplitue_extraction,
            waveforms_rms,
            denoised_waveforms_rms,
        ]

        # gather memory mode
        output = run_node_pipeline(recording, nodes, job_kwargs, gather_mode="memory")
        amplitudes, waveforms_rms, denoised_waveforms_rms = output

        num_peaks = peaks.shape[0]
        num_channels = recording.get_num_channels()
        if peak_source != peak_retriever_few:
            assert waveforms_rms.shape[0] == num_peaks
        assert waveforms_rms.shape[1] == num_channels

        if peak_source != peak_retriever_few:
            assert waveforms_rms.shape[0] == num_peaks
        assert waveforms_rms.shape[1] == num_channels

        # gather npy mode
        folder = cache_folder / f"pipeline_folder_{loop}"
        if folder.is_dir():
            shutil.rmtree(folder)
        output = run_node_pipeline(
            recording,
            nodes,
            job_kwargs,
            gather_mode="npy",
            folder=folder,
            names=["amplitudes", "waveforms_rms", "denoised_waveforms_rms"],
        )
        amplitudes2, waveforms_rms2, denoised_waveforms_rms2 = output

        amplitudes_file = folder / "amplitudes.npy"
        assert amplitudes_file.is_file()
        amplitudes3 = np.load(amplitudes_file)
        assert np.array_equal(amplitudes, amplitudes2)
        assert np.array_equal(amplitudes2, amplitudes3)

        waveforms_rms_file = folder / "waveforms_rms.npy"
        assert waveforms_rms_file.is_file()
        waveforms_rms3 = np.load(waveforms_rms_file)
        assert np.array_equal(waveforms_rms, waveforms_rms2)
        assert np.array_equal(waveforms_rms2, waveforms_rms3)

        denoised_waveforms_rms_file = folder / "denoised_waveforms_rms.npy"
        assert denoised_waveforms_rms_file.is_file()
        denoised_waveforms_rms3 = np.load(denoised_waveforms_rms_file)
        assert np.array_equal(denoised_waveforms_rms, denoised_waveforms_rms2)
        assert np.array_equal(denoised_waveforms_rms2, denoised_waveforms_rms3)

        # Test pickle mechanism
        for node in nodes:
            import pickle

            pickled_node = pickle.dumps(node)
            unpickled_node = pickle.loads(pickled_node)


def test_skip_after_n_peaks_and_recording_slices():
    recording, sorting = generate_ground_truth_recording(num_channels=10, num_units=10, durations=[10.0], seed=2205)

    # job_kwargs = dict(chunk_duration="0.5s", n_jobs=2, progress_bar=False)
    job_kwargs = dict(chunk_duration="0.5s", n_jobs=1, progress_bar=False)

    spikes = sorting.to_spike_vector()

    # create peaks from spikes
    sorting_analyzer = create_sorting_analyzer(sorting, recording, format="memory")
    sorting_analyzer.compute(["random_spikes", "templates"], **job_kwargs)
    extremum_channel_inds = get_template_extremum_channel(sorting_analyzer, peak_sign="neg", outputs="index")

    peaks = sorting_to_peaks(sorting, extremum_channel_inds, spike_peak_dtype)
    # print(peaks.size)

    node0 = PeakRetriever(recording, peaks)
    node1 = AmplitudeExtractionNode(recording, parents=[node0], param0=6.6, return_output=True)
    nodes = [node0, node1]

    # skip
    skip_after_n_peaks = 30
    some_amplitudes = run_node_pipeline(
        recording, nodes, job_kwargs, gather_mode="memory", skip_after_n_peaks=skip_after_n_peaks
    )
    assert some_amplitudes.size >= skip_after_n_peaks
    assert some_amplitudes.size < spikes.size

    # slices : 1 every 4
    recording_slices = divide_recording_into_chunks(recording, 10_000)
    recording_slices = recording_slices[::4]
    some_amplitudes = run_node_pipeline(
        recording, nodes, job_kwargs, gather_mode="memory", recording_slices=recording_slices
    )
    tolerance = 1.2
    assert some_amplitudes.size < (spikes.size // 4) * tolerance


# the following is for testing locally with python or ipython. It is not used in ci or with pytest.
if __name__ == "__main__":
    # folder = Path("./cache_folder/core")
    # test_run_node_pipeline(folder)

    test_skip_after_n_peaks_and_recording_slices()
