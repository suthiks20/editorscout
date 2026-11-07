#!/usr/bin/env python3
"""Build static location lists from manually downloaded GeoNames files.

Source: https://www.geonames.org/ export files (countryInfo.txt,
admin1CodesASCII.txt, cities1000.zip, and optional IN.zip), licensed under CC BY 4.0.
Download those files yourself, then run:
python scripts/build_locations.py --source-dir path/to/geonames
No network access is used by this script.
"""

import argparse
import csv
import json
import zipfile
from collections import defaultdict
from pathlib import Path

LOCATION_COUNTRIES = {"IN", "US", "GB", "CA", "AU", "AE", "SG", "DE", "FR", "LK"}
MAX_CITIES_PER_REGION = 40
NON_ISO_GEONAMES_CODES = {"AC", "AN", "CS", "TA", "XK"}
MANUAL_REGIONS = {
    "SG": {
        "Central Region": ["Singapore"],
        "East Region": ["Bedok", "Tampines", "Pasir Ris"],
        "North Region": ["Woodlands", "Yishun", "Sembawang"],
        "North-East Region": ["Hougang", "Sengkang", "Punggol"],
        "West Region": ["Jurong East", "Jurong West", "Clementi"],
    },
}


def read_countries(path):
    countries = []
    with path.open(encoding="utf-8") as source:
        for row in csv.reader(source, delimiter="\t"):
            if not row or row[0].startswith("#") or len(row) < 5:
                continue
            if row[0] not in NON_ISO_GEONAMES_CODES:
                countries.append({"code": row[0], "name": row[4]})
    return sorted(countries, key=lambda country: country["name"].casefold())


def read_regions(path):
    regions = {}
    with path.open(encoding="utf-8") as source:
        for row in csv.reader(source, delimiter="\t"):
            if len(row) >= 2:
                regions[row[0]] = row[1]
    return regions


def build_location_data(source_dir):
    regions = read_regions(source_dir / "admin1CodesASCII.txt")
    cities_by_region = defaultdict(list)
    archives = [
        (source_dir / "cities1000.zip", LOCATION_COUNTRIES),
    ]
    india_archive = source_dir / "IN.zip"
    if india_archive.exists():
        archives.append((india_archive, {"IN"}))
    for archive_path, allowed_countries in archives:
        with zipfile.ZipFile(archive_path) as archive:
            city_file = next(name for name in archive.namelist() if name.endswith(".txt"))
            with archive.open(city_file) as binary:
                for row in csv.reader(
                    (line.decode("utf-8") for line in binary), delimiter="\t"
                ):
                    if len(row) < 15 or row[8] not in allowed_countries:
                        continue
                    if archive_path == india_archive and row[6] != "P":
                        continue
                    region_code = f"{row[8]}.{row[10]}" if row[10] else ""
                    region_name = regions.get(region_code)
                    if not region_name:
                        continue
                    try:
                        population = int(row[14] or 0)
                    except ValueError:
                        population = 0
                    cities_by_region[(row[8], region_name)].append(
                        (population, row[2] or row[1])
                    )

    data = {}
    for country in LOCATION_COUNTRIES:
        country_regions = {}
        for (code, region_name), cities in cities_by_region.items():
            if code != country:
                continue
            names = []
            seen = set()
            for _population, name in sorted(
                cities, key=lambda item: (-item[0], item[1].casefold())
            ):
                key = name.casefold()
                if key not in seen:
                    seen.add(key)
                    names.append(name)
                if len(names) >= MAX_CITIES_PER_REGION:
                    break
            country_regions[region_name] = names

        if country == "IN":
            # Keep modern spellings where GeoNames has legacy variants.
            country_regions["Odisha"] = country_regions.pop(
                "Orissa", country_regions.get("Odisha", [])
            )
            country_regions["Uttarakhand"] = country_regions.pop(
                "Uttaranchal", country_regions.get("Uttarakhand", [])
            )
            country_regions.setdefault("Sikkim", []).extend(
                ["Soreng", "Pakyong", "Melli", "Ravangla", "Chungthang"]
            )
            country_regions["Sikkim"] = list(dict.fromkeys(country_regions["Sikkim"]))
        country_regions.update(MANUAL_REGIONS.get(country, {}))
        data[country] = {
            "states": [
                {"name": name, "cities": cities}
                for name, cities in sorted(
                    country_regions.items(), key=lambda item: item[0].casefold()
                )
            ]
        }
    return data


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=Path("static/data"))
    args = parser.parse_args()

    countries = read_countries(args.source_dir / "countryInfo.txt")
    write_json(args.output_dir / "countries.json", countries)
    for code, location_data in build_location_data(args.source_dir).items():
        write_json(args.output_dir / "locations" / f"{code}.json", location_data)


if __name__ == "__main__":
    main()
