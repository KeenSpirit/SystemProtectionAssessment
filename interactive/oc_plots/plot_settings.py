"""
PowerFactory settings for time-overcurrent coordination plots.

Page format, plot drawing options, axis extents, title and fault level
marker settings used by plot_relay.

Functions:
    setup_drawing_format: A4 landscape page.
    setup_toc_plot: Drawing options for the overcurrent plot.
    axis_settings: Log-log axes spanning the plotted fault levels.
    title_settings: Plot title.
    xvalue_settings: Fault level marker line.
"""

import math
from typing import List

from pf_config import pft
import assets as ast

# =============================================================================
# PLOT Level 2 SETTINGS
# =============================================================================

def setup_drawing_format(setgrfpage):
    """
    Create A4 landscape page format in project settings.

    Args:
        setgrfpage: The page's SetGrfpage drawing format object.
    """

    # Drawing Size
    setgrfpage.SetAttribute("iDrwFrm", 1)                           # Landscape
    # Format
    setgrfpage.SetAttribute("aDrwFrm", "A4")                        # A4


# =============================================================================
# PLOT Level 3 SETTINGS
# =============================================================================


def setup_toc_plot(pltovercurrent, f_type: str) -> None:
    """
    Configure the overcurrent plot's drawing options.

    Args:
        pltovercurrent: The plot's PltOvercurrent data source.
        f_type: 'Ground' shows earth elements, otherwise phase.
    """

    # Curves
    # =============================================================================

    # Plot features
    # Show coordination results
    pltovercurrent.SetAttribute("showCoordinationResults", 0)           # No
    # Select sub curves
    pltovercurrent.SetAttribute("enableSubCurves", 0)                   # No

    # Elements


    # Drawing options
    # =============================================================================

    # Voltage reference axes
    # Current unit
    pltovercurrent.SetAttribute("currentUnit", 0)                       # Primary ampere
    # Show voltages
    pltovercurrent.SetAttribute("shownVoltageRefAxes", 2)               # User-defined only
    # Voltage
    pltovercurrent.SetAttribute("userDefVoltageRefAxis", 11)            # 11kV

    # Show relays
    if f_type == 'Ground':
        pltovercurrent.SetAttribute("shownRelays", 3)                        # Ground
    else:
        pltovercurrent.SetAttribute("shownRelays", 1)                        # Phase
    # Reclose operation
    pltovercurrent.SetAttribute("recloserOperation", 0)                  # All
    # Cut curves at
    pltovercurrent.SetAttribute("cutCurves", 2)                         # Short-circuit/breaking current
    # Display results
    pltovercurrent.SetAttribute("showCalcResults", 1)                   # Currents
    # Consider breaker operating time
    pltovercurrent.SetAttribute("considerBreakerTime", 0)               # No
    # Show 'out of service' units
    pltovercurrent.SetAttribute("showOutOfServiceUnits", 0)             # No
    # Show grading margins while rag & drop
    pltovercurrent.SetAttribute("showGradingMarginDrag", 1)             # Yes
    # Grading margin +/-
    pltovercurrent.SetAttribute("gradingMarginDrag", 0.2)               # 0.2s
    # Points per curve
    pltovercurrent.SetAttribute("numPointsPerCurve", 200)               # 200


    # Text Format
    # =============================================================================

    # Number format
    # Format
    pltovercurrent.SetAttribute("numFormatMode", 1)                     # fixed decimals
    # Decimals
    pltovercurrent.SetAttribute("numFormatDecimals", 3)                 # 3
    # Exponent character
    pltovercurrent.SetAttribute("numExponentCase", 1)                   # "E"
    # Show signs for positive values
    pltovercurrent.SetAttribute("numFormatShowPlus", 0)                 # No

    # Label font
    # Label font
    # pltovercurrent.SetAttribute("editLabelFontButton", "Arial 10")      # Arial 10
    # Colour
    # pltovercurrent.SetAttribute("labelFontColor", 1)                    # Black?


    # Style and Layout
    # =============================================================================

    # Border and Background
    # Draw border
    pltovercurrent.SetAttribute("drawBorder", 0)                        # No
    # Border colour
    # pltovercurrent.SetAttribute("borderColor", 3)                     # Grey?
    # Border style
    # pltovercurrent.SetAttribute("borderStyle", 1)                     # Straight line
    # Border width
    #pltovercurrent.SetAttribute("borderWidth", "Hairline")             # Hairline
    # Fill background
    pltovercurrent.SetAttribute("fillBackground", 0)                    # No
    # Fill colour
    #pltovercurrent.SetAttribute("fillColor", 7)                        # White?

    # Curve area position (mm)
    # Auto-position Curve Area
    pltovercurrent.SetAttribute("autoPositionCurveArea", 1)             # Yes
    # Left
    # pltovercurrent.SetAttribute("curveAreaLeft", 0)                    # 0
    # Right
    # pltovercurrent.SetAttribute("curveAreaRight", 0)                   # 0
    # Bottom
    # pltovercurrent.SetAttribute("curveAreaBottom", 0)                  # 0
    # Top
    # pltovercurrent.SetAttribute("curveAreaTop", 0)                     # 0

    # Colour palette


def axis_settings(pltlinebarplot, f_type: str, devices: List[ast.Device]):
    """
    Configure TOC plot x- and y- axis extents.

    The current axis spans the first device's minimum to maximum fault
    level for the fault type, rounded out to whole decades. The caller
    checks both are positive first (see plot_relay.plot_bounds).

    Args:
        pltlinebarplot: The plot to configure.
        f_type: 'Ground' or 'Phase'.
        devices: Devices on the plot; the first sets the extents.
    """

    pltlinebarplot.SetAxisSharingLevelX(0)
    pltlinebarplot.SetAxisSharingLevelY(0)
    pltlinebarplot.SetScaleTypeX(1)
    pltlinebarplot.SetScaleTypeY(1)

    if f_type == 'Ground':
        x_min = _get_bound(devices[0].min_fl_pg, bound='Min')
        x_max = _get_bound(devices[0].max_fl_pg, bound='Max')
    else:
        max_fl_ph = max(devices[0].max_fl_2ph, devices[0].max_fl_3ph)
        x_min = _get_bound(devices[0].min_fl_2ph, bound='Min')
        x_max = _get_bound(max_fl_ph, bound='Max')
    pltlinebarplot.SetScaleX(x_min, x_max)
    pltlinebarplot.SetScaleY(0.01, 10)


def title_settings(pltlinebarplot, plot_name: str) -> None:
    """
    Configure TOC title bar.

    Args:
        pltlinebarplot: The plot to configure.
        plot_name: Title text.
    """

    plttitle = pltlinebarplot.GetTitleObject()
    # Show title
    plttitle.SetAttribute("showTitle", 1)                                   # Yes
    # Title
    plttitle.SetAttribute("titleString", [plot_name])                      # name


def xvalue_settings(
    constant: pft.VisXvalue,
    name: str,
    value: float
) -> None:
    """
    Configure fault current marker line settings.

    Args:
        constant: VisXvalue object to configure.
        name: Label text for the marker.
        value: Fault current value in Amperes.
    """
    constant.loc_name = name
    constant.label = 1
    constant.lab_text = [name]
    constant.show = 1                                   # Show with intersections
    constant.iopt_lab = 3                               # Label position
    constant.value = value
    constant.color = 1
    constant.width = 5
    constant.xis = 0                                    # Current axi


def _get_bound(num: float, bound: str) -> float:
    """
    Round fault level to nearest order of magnitude.

    Args:
        num: Fault level value.
        bound: 'Min' to round down, 'Max' to round up.

    Returns:
        Rounded value for axis limit.

    Raises:
        ValueError: If num is not positive (no fault level result).
    """
    if not num or num <= 0:
        raise ValueError(
            f"Cannot set a log axis limit from fault level {num!r}"
        )
    log_value = math.log10(num)

    if bound == 'Min':
        return 10 ** math.floor(log_value)
    else:
        return 10 ** math.ceil(log_value)
