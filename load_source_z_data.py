"""
Module to build a grid data dictionary

"""


def grid_data_import(region):

    grid_data = {}
    if region == 'Regional Models':
        # Open "2026 Fault Level Report (Ergon - Internal)_V1_1.xlsx" from folder called "Source Impedances" in root directory
        # Build nested dictionary grid_data as follows:
        # Outer nest keys: type = string.
        #       Keys are copied from values in Column D in the "Min Fault Level Report" tab.
        # Outer nest dictionary value: dictionary with three elements
        # Inner nest dictionary keys: "max", "min", "sn_min".
        # Inner nest dictionary value: list consisting of six elements.
        # The list value for key "max" is built as follows: The first element of the list is a string called "System Normal"
        #   Elements 2 to 6 are copied from Columns V-Z of the "Max-Max-Fault Level Report" tab for the first column D value that matches the outer nest key.
        #   If there is no match, the nested key, value pair is not created.
        # The list for key "min" is for key "max" is built as follows: The first element is a string called "System Normal"
        #   Elements 2 to 6 are copied from Columns V-Z of the "Min Fault Level Report" tab for the first column D value that matches the outer nest key.
        # The list for key "sn_min" is a duplicate of the list for key "min".
        # The following is an example nested dictionary for grid_data built when region == 'Regional Models':
        # grid_data_import["ABPO_66kV_TEF T1"] = {
        #     "max": ["System Normal", 2.03906, 0.40171, 0.99909, 2.01846, 0.26045],
        #     "min": ["System Normal", 1.54895, 0.53195, 1.0022, 1.77018, 0.40297],
        #     "sn_min": ["System Normal", 1.54895, 0.53195, 1.0022, 1.77018, 0.40297]
        # }
        pass

    else:
        # Open "grid_results_all.xlsx" from folder called "Source Impedances" in root directory
        # Build nested dictionary grid_data.
        # Outer nest keys: type = string.
        #       Keys are copied from unique values in Column D in the "Grid Results" tab.
        # Outer nest dictionary value: dictionary with three elements
        # Inner nest dictionary keys: "max", "min", "sn_min".
        # Inner nest dictionary value: list consisting of six elements.
        # The list value for key "max" is built as follows:
        # 1) locate the rows in the "Grid Results" tab with the Column B value that matches the outer key (there should be three rows)
        # 2) of these tows, select the first row with a Column D value that equals "Max"
        # 3) The elements of this list are copied directly from columns E to J in this row.
        # The list value for key "min" is built as follows:
        # 1) locate the rows in the "Grid Results" tab with the Column B value that matches the outer key (there should be three rows)
        # 2) of these tows, select the first row with a Column D value that equals "Min" and a Column E value that does not equal "System Normal"
        # 3) The elements of this list are copied directly from columns E to J in this row.
        # The list value for key "sn_min" is built as follows:
        # 1) locate the rows in the "Grid Results" tab with the Column B value that matches the outer key (there should be three rows)
        # 2) of these tows, select the first row with a Column D value that equals "Min" and a Column E value that equals "System Normal"
        # 3) The elements of this list are copied directly from columns E to J in this row.
        # The following is an example nested dictionary for grid_data built when region != 'Regional Models':
        # grid_data_import["MGP11kVB1_Term"] = {
        #     "max": ["MGP-TR1 OOS", 5190, 0.057958826, 1.000003, 14.125925, 0.001728],
        #     "min": ["F3701 OOS", 3855, 0.134663855, 1.000329, 11.721006, 0.004496],
        #     "sn_min": ["System Normal", 4126, 0.10063, 1.000380039, 12.49580002, 0.0045]
        # }
        pass

    return grid_data