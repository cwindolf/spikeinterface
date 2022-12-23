import numpy as np

from .recording_tools import get_channel_distances, get_noise_levels


def get_template_amplitudes(waveform_extractor, peak_sign: str = "neg", mode: str = "extremum"):
    """
    Get amplitude per channel for each unit.

    Parameters
    ----------
    waveform_extractor: WaveformExtractor
        The waveform extractor
    peak_sign: str
        Sign of the template to compute best channels ('neg', 'pos', 'both')
    mode: str
        'extremum':  max or min
        'at_index': take value at spike index

    Returns
    -------
    peak_values: dict
        Dictionary with unit ids as keys and template amplitudes as values
    """
    assert peak_sign in ("both", "neg", "pos")
    assert mode in ("extremum", "at_index")
    unit_ids = waveform_extractor.sorting.unit_ids

    before = waveform_extractor.nbefore

    peak_values = {}

    templates = waveform_extractor.get_all_templates(mode="average")
    for unit_ind, unit_id in enumerate(unit_ids):
        template = templates[unit_ind, :, :]

        if mode == "extremum":
            if peak_sign == "both":
                values = np.max(np.abs(template), axis=0)
            elif peak_sign == "neg":
                values = -np.min(template, axis=0)
            elif peak_sign == "pos":
                values = np.max(template, axis=0)
        elif mode == "at_index":
            if peak_sign == "both":
                values = np.abs(template[before, :])
            elif peak_sign == "neg":
                values = -template[before, :]
            elif peak_sign == "pos":
                values = template[before, :]

        peak_values[unit_id] = values

    return peak_values


def get_template_extremum_channel(waveform_extractor, peak_sign: str = "neg", mode: str = "extremum", outputs: str = "id"):
    """
    Compute the channel with the extremum peak for each unit.

    Parameters
    ----------
    waveform_extractor: WaveformExtractor
        The waveform extractor
    peak_sign: str
        Sign of the template to compute best channels ('neg', 'pos', 'both')
    mode: str
        'extremum':  max or min
        'at_index': take value at spike index
    outputs: str
        * 'id': channel id
        * 'index': channel index

    Returns
    -------
    extremum_channels: dict
        Dictionary with unit ids as keys and extremum channels (id or index based on 'outputs')
        as values
    """
    assert peak_sign in ("both", "neg", "pos")
    assert mode in ("extremum", "at_index")
    assert outputs in ("id", "index")

    unit_ids = waveform_extractor.sorting.unit_ids
    channel_ids = waveform_extractor.channel_ids

    peak_values = get_template_amplitudes(waveform_extractor, peak_sign=peak_sign, mode=mode)
    extremum_channels_id = {}
    extremum_channels_index = {}
    for unit_id in unit_ids:
        max_ind = np.argmax(peak_values[unit_id])
        extremum_channels_id[unit_id] = channel_ids[max_ind]
        extremum_channels_index[unit_id] = max_ind

    if outputs == "id":
        return extremum_channels_id
    elif outputs == "index":
        return extremum_channels_index


def get_template_channel_sparsity(
    waveform_extractor,
    method="best_channels",
    peak_sign="neg",
    outputs="id",
    num_channels=None,
    radius_um=None,
    threshold=5,
    by_property=None,
):
    """
    Get channel sparsity (subset of channels) for each template with several methods.

    Parameters
    ----------
    waveform_extractor: WaveformExtractor
        The waveform extractor
    method: str
        * "best_channels": N best channels with the largest amplitude. Use the 'num_channels' argument to specify the
                         number of channels.
        * "radius": radius around the best channel. Use the 'radius_um' argument to specify the radius in um
        * "threshold": thresholds based on template signal-to-noise ratio. Use the 'threshold' argument
                       to specify the SNR threshold.
        * "by_property": sparsity is given by a property of the recording and sorting(e.g. 'group').
                         Use the 'by_property' argument to specify the property name.
    peak_sign: str
        Sign of the template to compute best channels ('neg', 'pos', 'both')
    outputs: str
        * 'id': channel id
        * 'index': channel index
    num_channels: int
        Number of channels for 'best_channels' method
    radius_um: float
        Radius in um for 'radius' method
    threshold: float
        Threshold in SNR 'threshold' method
    by_property: object
        Property name for 'by_property' method

    Returns
    -------
    sparsity: dict
        Dictionary with unit ids as keys and sparse channel ids or indices (id or index based on 'outputs')
        as values
    """
    from spikeinterface.core.sparsity import ChannelSparsity
    
    if method == "best_channels":
        assert num_channels is not None
        sparsity = ChannelSparsity.from_best_channels(waveform_extractor, num_channels, peak_sign=peak_sign)
    elif method == "radius":
        assert radius_um is not None
        sparsity = ChannelSparsity.from_radius(waveform_extractor, radius_um, peak_sign=peak_sign)
    elif method == "threshold":
        assert threshold is not None
        sparsity = ChannelSparsity.from_threshold(waveform_extractor, threshold, peak_sign=peak_sign)
    elif method == "by_property":
        sparsity = ChannelSparsity.from_property(waveform_extractor, by_property)
    else:
        raise ValueError(f"get_template_channel_sparsity() method={method} do not exists")


    # handle output ids or indexes
    if outputs == "id":
        return sparsity.unit_id_to_channel_ids
    elif outputs == "index":
        return sparsity.unit_id_to_channel_indices
    elif outputs == "object":
        return sparsity


def get_template_extremum_channel_peak_shift(waveform_extractor, peak_sign: str = "neg"):
    """
    In some situations spike sorters could return a spike index with a small shift related to the waveform peak.
    This function estimates and return these alignment shifts for the mean template.
    This function is internally used by `compute_spike_amplitudes()` to accurately retrieve the spike amplitudes.

    Parameters
    ----------
    waveform_extractor: WaveformExtractor
        The waveform extractor
    peak_sign: str
        Sign of the template to compute best channels ('neg', 'pos', 'both')

    Returns
    -------
    shifts: dict
        Dictionary with unit ids as keys and shifts as values
    """
    sorting = waveform_extractor.sorting
    unit_ids = sorting.unit_ids

    extremum_channels_ids = get_template_extremum_channel(waveform_extractor, peak_sign=peak_sign)

    shifts = {}

    templates = waveform_extractor.get_all_templates(mode="average")
    for unit_ind, unit_id in enumerate(unit_ids):
        template = templates[unit_ind, :, :]

        chan_id = extremum_channels_ids[unit_id]
        chan_ind = waveform_extractor.channel_ids_to_indices([chan_id])[0]

        if peak_sign == "both":
            peak_pos = np.argmax(np.abs(template[:, chan_ind]))
        elif peak_sign == "neg":
            peak_pos = np.argmin(template[:, chan_ind])
        elif peak_sign == "pos":
            peak_pos = np.argmax(template[:, chan_ind])
        shift = peak_pos - waveform_extractor.nbefore
        shifts[unit_id] = shift

    return shifts


def get_template_extremum_amplitude(waveform_extractor, peak_sign: str = "neg", mode: str = "at_index"):
    """
    Computes amplitudes on the best channel.

    Parameters
    ----------
    waveform_extractor: WaveformExtractor
        The waveform extractor
    peak_sign: str
        Sign of the template to compute best channels ('neg', 'pos', 'both')
    mode: str
        Where the amplitude is computed
        'extremum':  max or min
        'at_index': take value at spike index

    Returns
    -------
    amplitudes: dict
        Dictionary with unit ids as keys and amplitudes as values
    """
    assert peak_sign in ("both", "neg", "pos")
    assert mode in ("extremum", "at_index")
    unit_ids = waveform_extractor.sorting.unit_ids

    before = waveform_extractor.nbefore

    extremum_channels_ids = get_template_extremum_channel(
        waveform_extractor, peak_sign=peak_sign, mode=mode
    )

    extremum_amplitudes = get_template_amplitudes(
        waveform_extractor, peak_sign=peak_sign, mode=mode
    )

    unit_amplitudes = {}
    for unit_id in unit_ids:
        channel_id = extremum_channels_ids[unit_id]
        best_channel = waveform_extractor.channel_ids_to_indices([channel_id])[0]
        unit_amplitudes[unit_id] = extremum_amplitudes[unit_id][best_channel]

    return unit_amplitudes


