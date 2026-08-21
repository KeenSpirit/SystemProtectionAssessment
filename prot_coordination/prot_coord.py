"""

For the range of fault currents seen by the primary device,
Calculate the primary device trip time
Calculate the back-up device trip time
Calculate the grading margin
Calculate  minimum grading margin
Store results
"""
import logging
logger = logging.getLogger(__name__)

import math
from typing import Dict, List, Optional, Tuple, Any

from pf_config import pft
from relays import current_conversion, elements, reclose, trip_time
from assets.enums import ElementType


FL_STEP_AMPS = 10


def prot_coordination(app: pft.Application, devices: List):
    fl_step = FL_STEP_AMPS

    for device in devices:
        dev_obj = device.obj
        total_trips = reclose.get_device_trips(dev_obj)

        logger.info(
            f"Protection coordination assessment: {dev_obj.loc_name}"
        )

        reclose.reset_reclosing(dev_obj)
        trip_count = 1
        worst_ph_coord_fl = None
        worst_ph_coord_margin = None
        worst_pg_coord_fl = None
        worst_pg_coord_margin = None

        max_phase_fl = trip_time.max_phase_fl(device)
        skip_ph_coord = device.min_device_2ph is None or max_phase_fl is None
        skip_pg_coord = device.min_device_pg is None or device.max_fl_pg is None

        if skip_ph_coord:
            logger.info(f"{dev_obj.loc_name} phase coordination skipped: "
                        f"missing fault level / pickup data")
        if skip_pg_coord:
            logger.info(f"{dev_obj.loc_name} ground coordination skipped: "
                        f"missing fault level / pickup data")
        if skip_ph_coord and skip_pg_coord:
            continue

        ph_min_fl = ph_max_fl = None
        pg_min_fl = pg_max_fl = None
        if not skip_ph_coord:
            ph_min_fl = int(device.min_device_2ph)
            ph_max_fl = int(max_phase_fl)
        if not skip_pg_coord:
            pg_min_fl = int(device.min_device_pg)
            pg_max_fl = int(device.max_fl_pg)

        # Eligible backups: same-cubicle devices are not backups, and a
        # device is never its own backup. Duplicate references to the
        # same PF object are dropped so the service-status capture below
        # cannot record an already-modified state as the original.
        eligible_bu_devices = []
        seen_bu_objs = set()
        for bu_device in device.us_devices:
            if bu_device.cubicle == device.cubicle:
                continue
            bu_obj = bu_device.obj
            if bu_obj is dev_obj or bu_obj in seen_bu_objs:
                continue
            seen_bu_objs.add(bu_obj)
            eligible_bu_devices.append(bu_device)

        bu_block_status = []
        try:
            # Assess each backup at its first-trip configuration - the
            # fastest state it can be in, and so the conservative basis
            # for a grading margin. This must precede the candidate
            # retrieval below, because get_prot_elements filters on
            # IsOutOfService at retrieval time. Fuses and relays without
            # a recloser return None from set_enabled_elements;
            # reset_block_service_status ignores None.
            for bu_device in eligible_bu_devices:
                reclose.reset_reclosing(bu_device.obj)
                bu_block_status.append(
                    reclose.set_enabled_elements(bu_device.obj)
                )

            # Backup candidates are independent of both the fault level
            # and the primary device's reclose trip (set_enabled_elements
            # touches only that device's own pdiselm elements), so
            # resolve them once per device rather than per fault level.
            bu_ph_candidates = []
            bu_pg_candidates = []
            for bu_device in eligible_bu_devices:
                if not skip_ph_coord:
                    bu_ph_candidates.append(
                        (bu_device, get_active_elements(bu_device, '2-Phase'))
                    )
                if not skip_pg_coord:
                    # Check whether the device is SWER. If so, BU device
                    # trip time must consider the FL seen by the bu device.
                    swer = swer_check(device, bu_device)
                    bu_fault_type = '2-Phase' if swer else 'Phase-Ground'
                    bu_pg_candidates.append(
                        (bu_device, swer, bu_fault_type,
                         get_active_elements(bu_device, bu_fault_type))
                    )

            while trip_count <= total_trips:
                block_service_status = reclose.set_enabled_elements(dev_obj)
                try:
                    if not skip_ph_coord:
                        fault_type = '2-Phase'
                        # Select only the elements capable of detecting the fault type
                        # and enabled for the current auto-reclose iteration
                        active_elements = get_active_elements(device, fault_type)

                        # Sample the grid plus the points either side of
                        # each of this device's instantaneous pickups,
                        # where its operate time steps discontinuously.
                        ph_fl_samples = sample_fault_levels(
                            ph_min_fl, ph_max_fl, fl_step,
                            hiset_currents(active_elements)
                        )

                        dev_fl_trip_register = {}
                        bu_fl_trip_register = {}
                        for fl in ph_fl_samples:
                            dev_fl_trip_register[fl] = None
                            for element in active_elements:
                                # Calculate protection operate time for element and fl
                                if element.GetClassName() == ElementType.FUSE.value:
                                    operate_time = trip_time.fuse_clear_time(element, fl)
                                else:
                                    element_current = current_conversion.get_measured_current(
                                        element, fl, fault_type)
                                    operate_time = trip_time.element_trip_time(element, element_current)
                                if not operate_time or operate_time <= 0:
                                    continue
                                if dev_fl_trip_register[fl] is None or operate_time < dev_fl_trip_register[fl]:
                                    dev_fl_trip_register[fl] = operate_time

                            bu_fl_trip_register[fl] = None
                            for bu_device, bu_active_elements in bu_ph_candidates:
                                for element in bu_active_elements:
                                    # Calculate protection operate time for element and fl
                                    if element.GetClassName() == ElementType.FUSE.value:
                                        operate_time = trip_time.fuse_clear_time(element, fl)
                                    else:
                                        element_current = current_conversion.get_measured_current(
                                            element, fl, fault_type)
                                        operate_time = trip_time.element_trip_time(element, element_current)
                                    if not operate_time or operate_time <= 0:
                                        continue
                                    if bu_fl_trip_register[fl] is None or operate_time < bu_fl_trip_register[fl]:
                                        bu_fl_trip_register[fl] = operate_time

                        for fl, dev_time in dev_fl_trip_register.items():
                            bu_time = bu_fl_trip_register.get(fl)
                            if dev_time is None or bu_time is None:
                                continue
                            coord_margin = bu_time - dev_time
                            if worst_ph_coord_margin is None or coord_margin < worst_ph_coord_margin:
                                worst_ph_coord_fl = fl
                                worst_ph_coord_margin = coord_margin
                    if not skip_pg_coord:
                        fault_type = 'Phase-Ground'
                        # Select only the elements capable of detecting the fault type
                        # and enabled for the current auto-reclose iteration
                        active_elements = get_active_elements(device, fault_type)

                        # As above, for this device's earth fault
                        # instantaneous pickups.
                        pg_fl_samples = sample_fault_levels(
                            pg_min_fl, pg_max_fl, fl_step,
                            hiset_currents(active_elements)
                        )

                        dev_fl_trip_register = {}
                        bu_fl_trip_register = {}
                        for fl in pg_fl_samples:
                            dev_fl_trip_register[fl] = None
                            for element in active_elements:
                                # Calculate protection operate time for element and fl
                                if element.GetClassName() == ElementType.FUSE.value:
                                    operate_time = trip_time.fuse_clear_time(element, fl)
                                else:
                                    element_current = current_conversion.get_measured_current(
                                        element, fl, fault_type)
                                    operate_time = trip_time.element_trip_time(element, element_current)
                                if not operate_time or operate_time <= 0:
                                    continue
                                if dev_fl_trip_register[fl] is None or operate_time < dev_fl_trip_register[fl]:
                                    dev_fl_trip_register[fl] = operate_time

                            bu_fl_trip_register[fl] = None
                            for (bu_device, swer, bu_fault_type,
                                 bu_active_elements) in bu_pg_candidates:
                                bu_fault_level = (
                                    swer_transform(device, bu_device, fl)
                                    if swer else fl
                                )
                                for element in bu_active_elements:
                                    # Calculate protection operate time for element and fl
                                    if element.GetClassName() == ElementType.FUSE.value:
                                        operate_time = trip_time.fuse_clear_time(element, bu_fault_level)
                                    else:
                                        element_current = current_conversion.get_measured_current(
                                            element, bu_fault_level, bu_fault_type)
                                        operate_time = trip_time.element_trip_time(element, element_current)
                                    if not operate_time or operate_time <= 0:
                                        continue
                                    if bu_fl_trip_register[fl] is None or operate_time < bu_fl_trip_register[fl]:
                                        bu_fl_trip_register[fl] = operate_time

                        for fl, dev_time in dev_fl_trip_register.items():
                            bu_time = bu_fl_trip_register.get(fl)
                            if dev_time is None or bu_time is None:
                                continue
                            coord_margin = bu_time - dev_time
                            if worst_pg_coord_margin is None or coord_margin < worst_pg_coord_margin:
                                worst_pg_coord_fl = fl
                                worst_pg_coord_margin = coord_margin
                finally:
                    reclose.reset_block_service_status(block_service_status)
                trip_count = reclose.trip_count(dev_obj, increment=True)

        finally:
            # Restore in reverse order so that if two backups ever share
            # an element, the earliest captured (true) original wins.
            for status in reversed(bu_block_status):
                reclose.reset_block_service_status(status)

        # Update device worst_coord_margin and worst_coord_fl
        device.ph_coord_fl = worst_ph_coord_fl
        device.ph_coord_margin = worst_ph_coord_margin
        device.pg_coord_fl = worst_pg_coord_fl
        device.pg_coord_margin = worst_pg_coord_margin

        # Leave the recloser at trip 1 rather than trips+1 so the
        # assessment does not persist counter drift into the model.
        reclose.reset_reclosing(dev_obj)


def get_active_elements(device, fault_type):
    device_obj = device.obj
    if device_obj.GetClassName() == ElementType.FUSE.value:
        active_elements = [device_obj]
    else:
        all_elements = elements.get_prot_elements(device_obj)
        active_elements = elements.get_active_elements(all_elements, fault_type)
    return active_elements


def swer_check(
        ds_device: "Device",
        us_device: "Device",
        ):
    """
    Return True if ds_device is SWER

    :param ds_device:
    :param us_device:
    :return:
    """

    # Check if transformation is needed
    voltage_mismatch = ds_device.l_l_volts != us_device.l_l_volts
    term_single_phase = ds_device.phases == 1
    device_multi_phase = us_device.phases > 1

    if voltage_mismatch and term_single_phase and device_multi_phase:
        return True
    return False


def swer_transform(
        ds_device: "Device",
        us_device: "Device",
        ds_device_fl_pg: float
    ) -> Tuple[int, str]:
    """
    Transform fault current for SWER (Single Wire Earth Return) systems.

    SWER lines operate at different voltages than the main distribution
    system. This function converts the fault current seen at a SWER
    terminal to what the upstream protection device sees.

    The transformation accounts for:
    - Voltage ratio between SWER and distribution system
    - Phase transformation (single-phase SWER to 3-phase distribution)

    Args:
        ds_device: Protection device dataclass.
        us_device: Protection device dataclass.
        ds_device_fl_pg: Phase-ground fault current at terminal in Amperes.

    Returns:
        Fault current as seen by the device in Amperes.
        Returns the original value if no SWER transformation needed.

    Transformation:
        device_fl = (term_volts × term_fl) / (device_volts × √3)

    Example:
        >>> device_current = swer_transform(device, swer_term, 500)
        >>> # If SWER at 12.7kV and device at 22kV:
        >>> # device_current = (12.7 × 500) / (22 × 1.732) ≈ 167A
    """

    if ds_device_fl_pg is None:
        return None

    # SWER transformation required
    us_device_fl = (
            (ds_device.l_l_volts * ds_device_fl_pg / us_device.l_l_volts) / math.sqrt(3)
    )

    return us_device_fl

def hiset_currents(prot_elements: List) -> List[float]:
    """
    Instantaneous pickup settings for the RelIoc elements in a list.

    These are treated as fault-level values. That is exact for
    elements whose measured current equals the fault current, and
    approximate for those with a conversion factor (NPS elements see
    If/3 for an earth fault, If/sqrt(3) for a phase fault). The
    residual error is bounded by the step grid, which brackets every
    discontinuity to within one step regardless.
    """
    return [
        element.GetAttribute("e:cpIpset")
        for element in prot_elements
        if element.GetClassName() == 'RelIoc'
    ]


def sample_fault_levels(
        min_fl: int,
        max_fl: int,
        fl_step: int,
        extra_currents: List
    ) -> List[int]:
    """
    Fault levels to evaluate between min_fl and max_fl.

    A fixed grid of fl_step, plus both interval endpoints and the
    points either side of each supplied pickup current. range()
    excludes max_fl unless the span is an exact multiple of the step,
    and trip time changes steeply either side of an instantaneous
    pickup, so those points are added explicitly rather than being
    left to fall between grid steps.
    """
    if max_fl < min_fl:
        return []

    samples = set(range(min_fl, max_fl + 1, fl_step))
    samples.add(max_fl)

    for current in extra_currents:
        if current is None:
            continue
        for value in (int(current) - 1, int(current)):
            if min_fl <= value <= max_fl:
                samples.add(value)

    return sorted(samples)