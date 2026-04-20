"""
Data consolidation and grouping utilities
"""
from typing import List, Dict
from collections import defaultdict
import pandas as pd


def group_by_commodity_code(items: List[Dict], group_by_origin: bool = False) -> Dict[str, List[Dict]]:
    """
    Group line items by commodity code, optionally also by country of origin.
    Items with blank / missing commodity codes are kept as individual entries
    (keyed by a unique placeholder) so they are NOT collapsed into one row.
    
    When group_by_origin=True, the key is "HS_CODE|COUNTRY" so that the same
    HS code from different countries produces separate declaration lines.
    
    Args:
        items: List of line item dictionaries
        group_by_origin: If True, also split groups by country of origin
        
    Returns:
        Dictionary mapping commodity codes (or code|country) to lists of items
    """
    grouped = defaultdict(list)
    blank_counter = 0
    
    for item in items:
        commodity_code = item.get('commodity_code', '').strip()
        if commodity_code and commodity_code != 'UNKNOWN':
            if group_by_origin:
                country = item.get('country_of_origin', '').strip()
                key = f"{commodity_code}|{country}" if country else commodity_code
            else:
                key = commodity_code
            grouped[key].append(item)
        else:
            # Give each blank-code item its own unique key so it isn't merged
            blank_counter += 1
            grouped[f'__BLANK_{blank_counter}__'].append(item)
    
    return dict(grouped)


def consolidate_items(items: List[Dict]) -> Dict:
    """
    Consolidate multiple items with same commodity code into summary.
    
    Args:
        items: List of items with same commodity code
        
    Returns:
        Consolidated item summary
    """
    if not items:
        return {}
    
    # Take common fields from first item
    consolidated = {
        'commodity_code': items[0].get('commodity_code'),
        'description': items[0].get('description', ''),
        'item_count': len(items),
        'total_quantity': 0,
        'total_value': 0,
        'total_net_weight': 0,
        'countries_of_origin': set(),
        'line_items': items
    }
    
    # Sum quantities and values
    for item in items:
        qty = item.get('quantity')
        if qty:
            try:
                consolidated['total_quantity'] += float(qty)
            except (ValueError, TypeError):
                pass
        
        # Parser outputs 'total_value', not 'value'
        value = item.get('total_value') or item.get('value')
        if value:
            try:
                consolidated['total_value'] += float(str(value).replace(',', ''))
            except (ValueError, TypeError):
                pass
        
        weight = item.get('net_weight')
        if weight:
            try:
                consolidated['total_net_weight'] += float(weight)
            except (ValueError, TypeError):
                pass
        
        origin = item.get('country_of_origin')
        if origin:
            consolidated['countries_of_origin'].add(origin)
    
    # Convert set to list for serialization
    consolidated['countries_of_origin'] = list(consolidated['countries_of_origin'])
    
    return consolidated


def create_consolidated_dataframe(grouped_items: Dict[str, List[Dict]], 
                                   hmrc_data: Dict[str, Dict] = None) -> pd.DataFrame:
    """
    Create a DataFrame with consolidated items and HMRC data.
    
    Args:
        grouped_items: Items grouped by commodity code
        hmrc_data: Optional HMRC API data for each commodity code
        
    Returns:
        Pandas DataFrame ready for export
    """
    rows = []
    
    for commodity_code, items in grouped_items.items():
        consolidated = consolidate_items(items)
        
        row = {
            'Commodity Code': commodity_code,
            'Description': consolidated.get('description', ''),
            'Item Count': consolidated.get('item_count', 0),
            'Total Quantity': consolidated.get('total_quantity', 0),
            'Total Value (£)': f"{consolidated.get('total_value', 0):.2f}",
            'Net Weight (kg)': f"{consolidated.get('total_net_weight', 0):.2f}",
            'Countries of Origin': ', '.join(consolidated.get('countries_of_origin', [])),
        }
        
        # Add HMRC data if available
        if hmrc_data and commodity_code in hmrc_data:
            hmrc_info = hmrc_data[commodity_code]
            row['HMRC Description'] = hmrc_info.get('description', '')
            row['Supplementary Units'] = hmrc_info.get('supplementary_units', 'N/A')
            
            # Add measure info
            import_measures = hmrc_info.get('import_measures', [])
            if import_measures:
                row['Import Duties'] = '; '.join([m.get('duty_expression', '') for m in import_measures[:3]])
        
        rows.append(row)
    
    df = pd.DataFrame(rows)
    return df


def export_to_excel(df: pd.DataFrame, 
                    username: str = "user",
                    direction: str = "export",
                    country: str = "",
                    job_id: str = "",
                    filename: str = "customs_invoice.xlsx"):
    """
    Export DataFrame to Excel with formatting and metadata.
    
    Args:
        df: DataFrame to export
        username: Job owner username
        direction: 'export' or 'import'
        country: Country code
        job_id: Job identifier
        filename: Output filename
        
    Returns:
        Path to created Excel file
    """
    from io import BytesIO
    import tempfile
    import os
    
    output_path = os.path.join(tempfile.gettempdir(), filename)
    
    with pd.ExcelWriter(output_path, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Consolidated Items')
        
        # Add metadata sheet
        metadata_df = pd.DataFrame({
            'Field': ['Job ID', 'Username', 'Direction', 'Country', 'Generated'],
            'Value': [job_id, username, direction, country, pd.Timestamp.now().strftime('%Y-%m-%d %H:%M:%S')]
        })
        metadata_df.to_excel(writer, index=False, sheet_name='Metadata')
        
        # Get the worksheet
        worksheet = writer.sheets['Consolidated Items']
        
        # Auto-adjust column widths
        for column in worksheet.columns:
            max_length = 0
            column_letter = column[0].column_letter
            for cell in column:
                try:
                    if len(str(cell.value)) > max_length:
                        max_length = len(str(cell.value))
                except:
                    pass
            adjusted_width = min(max_length + 2, 50)
            worksheet.column_dimensions[column_letter].width = adjusted_width
    
    return output_path
