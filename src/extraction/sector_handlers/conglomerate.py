"""Sector handler for diversified conglomerates (BRK-B, GE, SIE.DE, 6758.T)."""


CONGLOMERATE_SEGMENT_HINTS = """
For this diversified conglomerate, extract revenue and operating income for EACH distinct
business division/subsidiary as reported in the segment footnotes.

Common conglomerate segment types:
- Industrial / Manufacturing divisions
- Financial Services / Insurance / Reinsurance subsidiaries
- Technology / Digital divisions
- Energy / Infrastructure divisions
- Consumer / Media / Entertainment divisions
- Healthcare divisions

IMPORTANT NOTES:
- Berkshire Hathaway (BRK-B): Key segments are Insurance (GEICO, Gen Re, BH Reinsurance),
  BNSF Railroad, Berkshire Hathaway Energy, Manufacturing, McLane, Other.
  Insurance underwriting profit/loss ≠ revenue (use insurance premiums earned as revenue).
- GE: Segments are Aerospace, Renewable Energy, Power, Other.
- Siemens (SIE.DE): Segments are Digital Industries, Smart Infrastructure, Mobility,
  Healthineers, Financial Services, Other.
- Sony (6758.T): Segments are Game & Network Services (PlayStation), Music, Pictures (Movies/TV),
  Entertainment, Technology & Services, Imaging & Sensing, Financial Services.

Always use the MOST GRANULAR segment breakdown available in the filing.
"""


def get_prompt_hints() -> str:
    return CONGLOMERATE_SEGMENT_HINTS


def normalize_segment_name(raw: str) -> str:
    return raw.strip().title()
