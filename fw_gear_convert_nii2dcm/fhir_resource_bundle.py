"""FHIR Export from Flywheel

Export Flywheel Project As Patient Bundles
"""

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Dict, List, Tuple
from glob import glob
import os

import bson
import flywheel
import pandas as pd
from deepdiff.diff import DeepDiff
from fhir.resources.annotation import Annotation
from fhir.resources.bodystructure import BodyStructure, BodyStructureIncludedStructure
from fhir.resources.bundle import Bundle, BundleEntry
from fhir.resources.codeableconcept import CodeableConcept
from fhir.resources.codeablereference import CodeableReference
from fhir.resources.coding import Coding
from fhir.resources.device import Device, DeviceProperty
from fhir.resources.documentreference import DocumentReference
from fhir.resources.encounter import Encounter
from fhir.resources.extension import Extension
from fhir.resources.humanname import HumanName
from fhir.resources.identifier import Identifier
from fhir.resources.imagingstudy import ImagingStudy, ImagingStudySeries
from fhir.resources.patient import Patient
from fhir.resources.period import Period
from fhir.resources.reference import Reference
from flywheel_gear_toolkit import GearToolkitContext
from fw_file.dicom import DICOMCollection

log = logging.getLogger(__name__)

"""## Set parameters for the FHIR export"""
DICOM_TAG_SERIES = [
    "StudyInstanceUID",
    "StudyDate",
    "PatientID",
    "StudyTime",
    "StudyDescription",
    "SeriesInstanceUID",
    "SeriesNumber",
    "Modality",
    "SeriesDate",
    "SeriesTime",
    "SeriesDescription",
    "BodyPartExamined",
    "DeidentificationMethod",
    "Manufacturer",
    "ManufacturerModelName",
    "DeviceSerialNumber",
    "SoftwareVersions",
    "ImplentationVersionName",
    "MagneticFieldStrength",
]
DICOM_TAG_EXPORT = [
    "BodyPartExamined",
    "Manufacturer",
    "ManufacturerModelName",
    "MagneticFieldStrength",
]


"""## Define various mapping functions to convert flywheel data to FHIR Resources"""
_file_path = Path(__file__).parent.absolute()
RACE_DF = pd.read_csv(_file_path / "resources/hl7.CodeSystem.v3-Race.csv", comment="#")
ETHNICITY_DF = pd.read_csv(
    _file_path / "resources/hl7.CodeSystem.v3-Ethnicity.csv", comment="#"
)
BIRTH_SEX_DF = pd.read_csv(
    _file_path / "resources/hl7.CodeSystem.v3-AdministrativeGender.csv", comment="#"
)

DICOM_SNOMED_MAP_DF = pd.read_csv(
    _file_path
    / "resources"
    / "Table.L-1.Corresponding.Codes.and.Terms.for.Human.Use.csv",
    comment="#",
    dtype={"Code Value": str},
)


def make_race_extension(race: str):
    """Create a FHIR Extension for race.

    http://hl7.org/fhir/us/core/StructureDefinition/us-core-race"

    Args:
        race (str): The race of the subject.

    Returns:
        Extension: A FHIR Extension for race.
    """
    race_ = RACE_DF[RACE_DF["Display"] == race]
    url = "ombCategory"
    race_system = "urn:oid:2.16.840.1.113883.6.238"
    if len(race_) != 0:
        code = race_.iloc[0].Code
        display = race_.iloc[0].Display
    else:
        code = "2131-1"
        display = "Other Race"
    extension = Extension(
        url="http://hl7.org/fhir/us/core/StructureDefinition/us-core-race",
        extension=[
            Extension(
                url=url,
                valueCoding=Coding(system=race_system, code=code, display=display),
            ),
            Extension(url="text", valueString=display),
        ],
    )
    return extension


def make_ethnicity_extension(ethnicity: str):
    """Create a FHIR Extension for ethnicity.

    http://hl7.org/fhir/us/core/StructureDefinition/us-core-ethnicity

    Args:
        ethnicity (str): The ethnicity of the subject.

    Returns:
        Extension: A FHIR Extension for ethnicity.
    """
    ethnicity_ = ETHNICITY_DF[ETHNICITY_DF["Display"] == ethnicity]
    url = "ombCategory"
    race_system = "urn:oid:2.16.840.1.113883.6.238"
    if len(ethnicity_) != 0:
        code = ethnicity_.iloc[0].Code
        display = ethnicity_.iloc[0].Display
    else:
        code = "2186-5"
        display = "Not Hispanic or Latino"
    extension = Extension(
        url="http://hl7.org/fhir/us/core/StructureDefinition/us-core-ethnicity",
        extension=[
            Extension(
                url=url,
                valueCoding=Coding(system=race_system, code=code, display=display),
            ),
            Extension(url="text", valueString=display),
        ],
    )
    return extension


def make_birth_sex_extension(sex: str):
    """Create a FHIR Extension for birth sex.

    Args:
        sex (str): Birth sex of the subject.

    Returns:
        Extension: The FHIR Extension for birth sex.
    """
    birth_sex = BIRTH_SEX_DF[BIRTH_SEX_DF["Display"].str.lower() == sex.lower()]
    url = "http://hl7.org/fhir/us/core/StructureDefinition/us-core-birthsex"
    if len(birth_sex) != 0:
        code = birth_sex.iloc[0].Code
    else:
        code = "UNK"

    return Extension(url=url, valueCode=code)


# TODO: Ideally convert ids to urls per FHIR expectation
# TODO: probably need to create a formal coding scheme for gender
def get_patient_resource(subject: flywheel.Subject) -> Patient:
    """Construct a FHIR Patient resource from a Flywheel Subject

    https://build.fhir.org/patient.html

    Args:
        subject (flywheel.Subject): Flywheel Subject to convert to FHIR Patient

    Returns:
        Patient: FHIR Patient resource
    """
    subject_info = subject.info

    research_study_code = (
        subject_info.get("qc", {})
        .get("include", {})
        .get("research_study_code", "Unknown")
    )

    system_url = f"https://includedcc.org/fhir/{research_study_code}/researchstudy"
    # TODO: How to exclude this subject if we have not defined a subject_study_code?
    # TODO: Handle this without the `info.qc.include` key. Should be more generic.
    str_identifier = (
        subject_info.get("qc", {})
        .get("include", {})
        .get("subject_study_code", subject.label)
    )

    identifiers = [
        Identifier(
            use="secondary",
            system=system_url,
            value=str_identifier,
        ),
        Identifier(
            use="official",
            system=system_url,
            value=subject.label,
        ),
    ]
    subject_extension_keys = [
        # "label",
        # "cohort",
    ]
    human_name = HumanName(
        family=subject.lastname if subject.lastname else None,
        given=[subject.firstname] if subject.firstname else None,
    )
    subj_sex = subject.sex if subject.sex else "unknown"
    data = {
        "id": subject.label,  # This will be the INCLUDE GlobalID
        "name": [human_name],
        "gender": subj_sex,
        "birthDate": subject.date_of_birth,
        "deceasedBoolean": subject.state == "Dead",
        "extension": [
            make_race_extension(subject["race"]),
            make_ethnicity_extension(subject["ethnicity"]),
            make_birth_sex_extension(subj_sex),
        ],
    }

    data["extension"].extend(
        [Extension(url=key, valueCode=subject[key]) for key in subject_extension_keys]
    )
    data["identifier"] = identifiers

    patient = Patient(**data)
    return patient


def create_age_at(
    patient: Patient, session: flywheel.Session = None, series: dict = None
):
    """Create "age at" in days for a FHIR resource.

    Args:
        patient (Patient): A patient resource with a valid birthDate.
        session (flywheel.Session): A Flywheel Session to get the timestamp from.
        series (dict): A dictionary containing the study information from the dicom

    Returns:
        dict: FHIR Extension dictionary for age at. If constraints are not met, None.
    """
    if patient and patient.birthDate:
        if session:
            date_of_encounter = session.timestamp
        elif series:
            if series.get("SeriesDate") and series.get("SeriesTime"):
                date_of_encounter = datetime.strptime(
                    series["SeriesDate"] + series["SeriesTime"],
                    "%Y%m%d%H%M%S.%f"
                    if "." in series["SeriesTime"]
                    else "%Y%m%d%H%M%S",
                ).replace(tzinfo=timezone.utc)
            elif series.get("SeriesDate"):
                date_of_encounter = datetime.strptime(
                    series["SeriesDate"], "%Y%m%d"
                ).replace(tzinfo=timezone.utc)
            else:
                date_of_encounter = None
        else:
            date_of_encounter = None

        if date_of_encounter:
            age_in_days_at_encounter = (
                date_of_encounter
                - datetime.combine(patient.birthDate, datetime.min.time(), timezone.utc)
            ).days  # .total_seconds() / 86400.0 -- This will give a decimal date
        else:
            age_in_days_at_encounter = None

    elif session or series:
        if session:
            age_in_days_at_encounter = (
                int(session.age_days) if session.age_days else None
            )
        elif series:
            age_in_days_at_encounter = series.get("age_in_days_at_session")
    else:
        age_in_days_at_encounter = None

    if not age_in_days_at_encounter:
        return None
    else:
        return {
            "extension": [
                {
                    "url": "http://hl7.org/fhir/StructureDefinition/cqf-relativeDateTime",
                    "extension": [
                        {
                            "url": "target",
                            "valueReference": {"reference": f"Patient/{patient.id}"},
                        },
                        {"url": "targetPath", "valueString": "birthDate"},
                        {"url": "relationship", "valueCode": "after"},
                        {
                            "url": "offset",
                            "valueDuration": {
                                "value": age_in_days_at_encounter,
                                "unit": "d",
                                "system": "http://unitsofmeasure.org",
                                "code": "days",
                            },
                        },
                    ],
                }
            ]
        }


def get_encounter_resource(patient: Patient, session: flywheel.Session) -> Encounter:
    """Create a FHIR Encounter resource from a Flywheel Session

    https://build.fhir.org/encounter.html

    Args:
        patient (Patient): FHIR Patient resource
        session (flywheel.Session): Flywheel Session to convert to FHIR Encounter

    Returns:
        Encounter: FHIR Encounter resource
    """
    # Period defining when the encounter took place:
    period = Period(_start=create_age_at(patient, session=session))
    data = {
        "id": session["_id"],
        "status": "completed",
        "class": [
            CodeableConcept(
                coding=[
                    Coding(
                        code="Session",
                        # TODO: create a url to host the flywheel coding the scheme.
                        #       This URL is a placeholder.
                        system="http://flywheel.io/CodeSystem/v1-codes",
                    )
                ]
            )
        ],
        "actualPeriod": period,
        "subject": {"reference": "Patient/" + patient.id},
    }
    encounter = Encounter(**data)
    return encounter


def get_dcm_device_resource(series: Dict):
    """Create a FHIR Device resource from a DICOM Series

    https://build.fhir.org/device.html

    Args:
        series (dict): A dictionary containing the study information from the dicom
                       headers

    Returns:
        Device: A FHIR Device resource
    """
    # TODO: Provide a hash of the DevidceSerialNumber to use as the id. This will
    #       provide a consistent representation of the device without exposing the
    #       serial number.
    if series.get("DeviceSerialNumber"):
        hashed_serial_number = hashlib.md5(
            series.get("DeviceSerialNumber").encode("utf-8")
        ).hexdigest()
    else:
        hashed_serial_number = bson.ObjectId().__str__()
    id = hashed_serial_number
    data = {
        "id": id,
        "identifier": [
            Identifier(
                system="urn:dicom:uid",
                value=f"urn:oid:{id}",
            )
        ],
        "manufacturer": series.get("Manufacturer"),
        "modelNumber": series.get("ManufacturerModelName"),
        "serialNumber": (
            hashed_serial_number if series.get("DeviceSerialNumber") else "Unknown"
        ),
        "version": [{"value": str(series.get("SoftwareVersions"))}]
        if series.get("SoftwareVersions")
        else None,
    }
    if series.get("MagneticFieldStrength"):
        data["property"] = [
            DeviceProperty(
                type=CodeableConcept(
                    coding=[
                        Coding(
                            system="http://bioportal.bioontology.org/ontologies/DCM",
                            code="130542",
                        )
                    ]
                ),
                valueString=series.get("MagneticFieldStrength"),
            )
        ]

    device = Device(**data)
    return device


def get_body_structure_resource(patient: Patient, body_part: str) -> BodyStructure:
    """Create a BodyStructure Resource that can be referenced.

    See https://hl7.org/fhir/bodystructure.html.
    Args:
        patient (Patient): The patient resource this is associated with.
        body_part (str): The DICOM Body Part Examined tag.

    Returns:
        BodyStructure: The Resource for the BodyStructure.
    """
    body_part_ = DICOM_SNOMED_MAP_DF[
        DICOM_SNOMED_MAP_DF["Body Part Examined"] == body_part
    ]
    if len(body_part_) != 0:
        code = body_part_.iloc[0]["Code Value"]
        display = body_part_.iloc[0]["Code Meaning"]
        body_part_ = body_part
    else:
        code = "000000000"
        display = "Unknown Body Part Examined"
        body_part_ = "UNKNOWN"
    data = {
        "id": body_part_,
        "includedStructure": [
            BodyStructureIncludedStructure(
                structure=CodeableConcept(
                    coding=[
                        Coding(
                            system="http://snomed.info/sct", code=code, display=display
                        )
                    ],
                    text=body_part_,
                )
            )
        ],
        "patient": Reference(reference=f"Patient/{patient.id}"),
    }
    body_structure = BodyStructure(**data)

    return body_structure


def get_document_reference(
    file_: Dict,
    series_data: Dict,
) -> DocumentReference:
    document_reference = DocumentReference(
        id=file_["file_id"],
        status="current",
        description=file_["file_name"],
        subject={"reference": f"Patient/{series_data['PatientID']}"},
        content=[
            {
                "attachment": {
                    "url": f"file://{file_['path']}".replace(" ", "%20"),
                }
            }
        ],
        extension=[
            {
                "url": "http://hl7.org/fhir/StructureDefinition/series-reference",
                "valueReference": {
                    "reference": f"ImagingStudy/{series_data['StudyInstanceUID']}"
                    f"/series/{series_data['SeriesInstanceUID']}"
                },
            }
        ],
    )
    series_reference = Extension(
        url="http://example.com/fhir/StructureDefinition/series-document-reference",
        valueReference=Reference(
            id=file_["file_name"],
            reference=f"DocumentReference/{file_['file_id']}",
        ),
    )
    return document_reference, series_reference


def get_imaging_study_series_resource(
    patient: Patient,
    series: Dict,
    include_bodystructure: bool = False,
) -> Tuple[ImagingStudySeries, BodyStructure, Device, Dict]:
    """Get an ImagingStudySeries from a series dictionary.

    Args:
        patient (Patient): FHIR Patient resource
        series (dict): A dictionary containing the study information from the dicom
                       headers

    Returns:
        ImagingStudy: A FHIR ImagingStudy resource
    """
    if (
        series.get("Intent")
        and series.get("Measurement")
        and series.get("Manufacturer")
    ):
        series_description = (
            f"{series['Manufacturer']}_"
            f"{'.'.join(series['Intent'])}_"
            f"{'.'.join(series['Measurement'])}"
        )
    else:
        log.warning(
            f"The DICOM series {series['SeriesInstanceUID']} should have "
            "been classified first."
        )
        series_description = "Unknown"

    body_structure = get_body_structure_resource(
        patient, series.get("BodyPartExamined")
    )

    device = get_dcm_device_resource(series)

    document_references = {}

    # Ensure PatientID is set.
    if not series.get("PatientID"):
        series["PatientID"] = patient.id

    series_data = {
        "uid": series["SeriesInstanceUID"],
        "number": series["SeriesNumber"],
        "modality": CodeableConcept(
            coding=[
                Coding(
                    code=series["Modality"],
                    system="http://dicom.nema.org/resources/ontology/DCM",
                )
            ]
        ),
        "description": series_description,
        "numberOfInstances": series["numberOfInstances"],
        "performer": [
            {
                "function": {
                    "coding": [
                        Coding(
                            system="http://terminology.hl7.org/CodeSystem/v3-ParticipationType",
                            code="PRF",
                        )
                    ]
                },
                "actor": Reference(reference=f"Device/{device.id}"),
            }
        ],
        "endpoint": [Reference(reference="http://DRS.server.com/PLACEDHOLDER")],
    }
    if include_bodystructure:
        series_data["bodySite"] = CodeableReference(
            reference=Reference(reference=f"BodyStructure/{body_structure.id}")
        )

    else:
        concept = body_structure.includedStructure[0].structure
        series_data["bodySite"] = CodeableReference(concept=concept)

    if series_time_days := create_age_at(patient, series=series):
        series_data.update({"_started": series_time_days})

    # Create a document reference for the primary file
    document_reference, series_reference = get_document_reference(series, series)
    document_references[series["file_id"]] = document_reference
    series_data["extension"] = []
    series_data["extension"].append(series_reference)

    # Create a document reference for each additional file
    if series.get("additional_files"):
        for file_ in series["additional_files"]:
            document_reference, series_reference = get_document_reference(file_, series)
            document_references[file_["file_id"]] = document_reference
            series_data["extension"].append(series_reference)
    return (
        ImagingStudySeries(**series_data),
        body_structure,
        device,
        document_references,
    )


# TODO: Flesh out the properties from the dicom tags -
# will need to add the relevant ones to the study object
def get_imaging_study_resource(
    patient: Patient,
    encounter: Encounter,
    study: Dict[str, List],
    include_bodystructure: bool = False,
) -> Tuple[ImagingStudy, Dict, Dict, Dict]:
    """Create a FHIR ImagingStudy resource from a single DICOM StudyInstance

    https://build.fhir.org/imagingstudy.html

    Args:
        patient (Patient): FHIR Patient resource
        encounter (Encounter): FHIR Encounter resource
        study (dict): A dictionary containing the study information from dicom headers
        include_bodystructure (bool): Whether to include BodyStructure resources in the bundle

    Returns:
        Tuple: ImagingStudy, BodyStructures, Devices, DocumentReferences
    """
    # if the key "series" is not present in study, or if it is empty, just return:
    if not study.get("series"):
        print("Warning: no series in study")
        return None, None, None, None

    # if there is more than a single StudyInstanceUID per series, return an error:
    if len(set(series["StudyInstanceUID"] for series in study["series"])) > 1:
        raise RuntimeError("More than one StudyInstanceUID found in this study.")

    # if there is more than a single DICOM modality per series, return an error:
    if len(set(series["Modality"] for series in study["series"])) > 1:
        raise RuntimeError("More than one DICOM modality found in this study.")

    # TODO: These next three resources may be better encapsulated in a Class Object
    #       instead of passing them around so much.
    # A keyed reference to body structures included in the imaging study.
    body_structures = {}

    # A keyed reference to devices used in the imaging study.
    devices = {}

    # A keyed reference to documents referenced in the imaging study.
    document_references = {}

    # the StudyInstanceUID and Modality, as well as the device properties, should be the
    # same for all elements of the study, so just pick the first one as sample_series:
    sample_series = study["series"][0]

    data = {
        "identifier": [
            Identifier(  # see: https://www.hl7.org/fhir/imagingstudy.html#notes
                system="urn:dicom:uid",
                value=f"urn:oid:{sample_series['StudyInstanceUID']}",
            )
        ],
        "status": "available",
        "subject": {"reference": "Patient/" + patient.id},
        # # This is a placeholder for how these should be handled.
        # # It may be a different extension, or a different resource.
        # "extension": [
        #     Extension(
        #         url="http://hl7.org/fhir/StructureDefinition/_datatype",
        #         id=tag,
        #         valueString=sample_series[tag],
        #     )
        #     for tag in DICOM_TAG_EXPORT
        #     if tag in sample_series.keys()
        # ],
        "series": [],
    }
    if encounter:
        data["encounter"] = {"reference": "Encounter/" + encounter.id}

    if study_date_days := create_age_at(patient, series=sample_series):
        data.update({"_started": study_date_days})

    if "StudyDescription" in sample_series:
        data.update(
            {
                "note": [Annotation(**{"text": sample_series["StudyDescription"]})],
            }
        )
    # Populate the ImagingStudy.series list
    for series in study["series"]:
        (
            imaging_study_series_resource,
            body_structure,
            device,
            document_references_,
        ) = get_imaging_study_series_resource(patient, series, include_bodystructure)
        data["series"].append(imaging_study_series_resource)
        if not body_structures.get(body_structure.id) and include_bodystructure:
            body_structures[body_structure.id] = body_structure

        if not devices.get(device.id):
            devices[device.id] = device

        # Assuming that the document references are unique to each series
        document_references.update(document_references_)

    # Now that we have all the series, get the numberOfSeries and all the modalities
    # present and set them as a list for the imagingstudy.modality and numberOfSeries:
    data["numberOfSeries"] = len(data["series"])
    # Note: to get all the different modality codes in the series,there can be more than
    # one modality per series, the way the "coding" in CodeableConcept is defined.
    modality_codes = list(
        set(m.code for series in data["series"] for m in series.modality.coding)
    )
    data["modality"] = [
        CodeableConcept(
            coding=[
                Coding(
                    code=modality_code,
                    system="http://dicom.nema.org/resources/ontology/DCM",
                )
                for modality_code in modality_codes
            ]
        )
    ]

    imaging_study = ImagingStudy(**data)
    return imaging_study, body_structures, devices, document_references


def get_file_path_from_acquisition(
    client: flywheel.Client, file_: flywheel.FileEntry
) -> str:
    """Get the path to a file from a Flywheel Acquisition

    Args:
        client: Flywheel Client
        file_ (flywheel.FileEntry): Flywheel FileEntry to get the path for

    Returns:
        str: The path to the file
    """
    path_str = ""
    for parent_str in ["project", "subject", "session", "acquisition"]:
        if file_.parents.get(parent_str):
            parent = client.get(file_.parents[parent_str])
            path_str += f"{parent.label}/"
    path_str += file_.name

    return path_str


def get_image_studies_from_session(
    client: flywheel.Client,
    session: flywheel.Session,
) -> Dict[str, Dict[str, List]]:
    """Create a dictionary of dicom studies from a Flywheel Session

    It returns a dictionary with the different DICOM StudyInstances in a session as
    different keys (typically there will be only one). The value for that key is, in
    turn a dictionary with a key named "series", which is a list of the different DICOM
    series in the Study (session)

    Args:
        session (flywheel.Session): Flywheel Session to extract dicom studies from

    Returns:
        dict: A dictionary of dicom studies keyed by StudyInstanceUIDs
    """
    studies = {}
    for acquisition in session.acquisitions.iter():
        for file_ in acquisition.files:
            if file_.type in ["dicom","nifti"]:
                file_ = file_.reload()
                dicom_info = {}
                if file_.info.get("header", {}).get("dicom"):
                    # the DICOM header was extracted using the metadata-importer gear:
                    dicom_info = file_.info["header"]["dicom"]
                elif file_.info.get("StudyInstanceUID"):
                    # the DICOM header was extracted using old DICOM-classifier gear:
                    dicom_info = file_.info
                else:
                    if file_.type == "dicom":
                        with TemporaryDirectory() as tmpdir:
                            file_path = Path(tmpdir) / file_.name
                            file_.download(file_path)
                            dcms = DICOMCollection.from_zip(file_path)
                            dicom_info = {tag: dcms.get(tag) for tag in DICOM_TAG_SERIES}
                    
                    elif file_.type == "nifti":
                        # get metadata from corresponding JSON sidecar within same acquisition
                        nii_file_name = file_.name
                        if '.gz' in nii_file_name:
                            json_file_name = nii_file_name.replace('.nii.gz','.json')
                        else:
                            json_file_name = nii_file_name.replace('.nii','.json')
                        acquisition = acquisition.reload()
                        try:
                            acquisition.download_file(json_file_name, json_file_name)
                            with open(json_file_name) as json_file:
                                dicom_info = json.load(json_file)
                            # add in dummy values for requisite fields
                            if not dicom_info.get("StudyInstanceUID"):
                                dicom_info["StudyInstanceUID"] = '0000000'
                            if not dicom_info.get("SeriesInstanceUID"):
                                dicom_info["SeriesInstanceUID"] = '0000000'
                        except:
                            log.warning(f'WARNING: Unable to find source DICOM metadata in file.info, \
                                        file.info.header.dicom or JSON sidecar \
                                        (file: {session.subject.label}/{session.label}/{nii_file_name})')

                series = {
                    "id": file_.file_id,
                    "aquisition_id": acquisition["_id"],
                    "acquisition_label": acquisition["label"],
                    "file_name": file_.name,
                    "file_id": file_.file_id,
                    "path": get_file_path_from_acquisition(client, file_),
                }
                if session.age_days:
                    series["age_in_days_at_session"] = int(session.age_days)
                series.update(
                    {
                        tag: dicom_info[tag]
                        for tag in DICOM_TAG_SERIES
                        if dicom_info.get(tag)
                    }
                )

                if not series.get("SeriesNumber"):
                    series["SeriesNumber"] = 0

                if not series.get("StudyInstanceUID"):
                    series["StudyInstanceUID"] = '0000000'

                if not series.get("SeriesInstanceUID"):
                    series["SeriesInstanceUID"] = '0000000'

                if not series.get("Modality"):
                    series["Modality"] = file_.modality

                for tag in ["Measurement", "Intent"]:
                    if file_.classification.get(tag) and file_.classification[tag]:
                        series[tag] = file_.classification[tag]

                if file_.type == "dicom":
                    number_of_instances = file_.get("zip_member_count")
                    if not number_of_instances:
                        number_of_instances = len(file_.get_zip_info().members)

                    series["numberOfInstances"] = number_of_instances
                else:
                    series["numberOfInstances"] = 0


                if series["StudyInstanceUID"] not in studies:
                    studies[series["StudyInstanceUID"]] = {"series": []}

                studies[series["StudyInstanceUID"]]["series"].append(series)

                # Check if series is a Diffusion Measurement
                if series.get("Intent") and series.get("Measurement"):
                    series["additional_files"] = []
                    # reference nifti, bval, and bvec files
                    for file_ in acquisition.files:
                        if file_.type in [
                            "nifti",
                            "bval",
                            "bvec",
                            "source code",
                        ]:
                            file_spec = {
                                "file_name": file_.name,
                                "file_id": file_.file_id,
                                "path": get_file_path_from_acquisition(client, file_),
                            }
                            series["additional_files"].append(file_spec)

    return studies


def get_patient_bundle(
    client: flywheel.Client,
    subject: flywheel.Subject,
    include_encounter: bool,
    include_bodystructure: bool,
) -> Bundle:
    """Create a FHIR Bundle resource from a Flywheel Subject

    Args:
        client (flywheel.Client): The Flywheel Client
        subject (flywheel.Subject): Flywheel Subject to convert to FHIR Bundle
        include_encounter (bool): Whether to include Encounter resources in the bundle
        include_bodystructure (bool): Whether to include BodyStructure resources in the bundle

    Returns:
        Bundle: FHIR Bundle Resource
    """
    subject = subject.reload()
    entries = []
    body_structures = []
    devices = []
    document_references = []
    patient = get_patient_resource(subject)
    entries.append(BundleEntry(resource=patient, fullUrl=f"Patient/{patient.id}"))
    for session in subject.sessions.iter():
        if include_encounter:
            encounter = get_encounter_resource(patient, session)
            entries.append(
                BundleEntry(resource=encounter, fullUrl=f"Encounter/{encounter.id}")
            )
        else:
            encounter = None
        studies = get_image_studies_from_session(client, session)
        for study_info in studies.values():
            (
                imaging_study,
                body_structures_,
                devices_,
                document_references_,
            ) = get_imaging_study_resource(
                patient, encounter, study_info, include_bodystructure
            )
            entries.append(
                BundleEntry(
                    resource=imaging_study,
                    fullUrl=f"ImagingStudy/{imaging_study.identifier[0].value[8:]}",
                )
            )
            # Add BodyStructures as a separate resource
            _ = [
                body_structures.append(bs)
                for k, bs in body_structures_.items()
                if k not in [b.id for b in body_structures]
            ]

            _ = [
                devices.append(d)
                for k, d in devices_.items()
                if k not in [d.id for d in devices]
            ]

            _ = [
                document_references.append(ds) for k, ds in document_references_.items()
            ]

    # Add body structures to the bundle
    entries.extend(
        [
            BundleEntry(
                resource=body_structure,
                fullUrl=f"BodyStructure/{body_structure.id}",
            )
            for body_structure in body_structures
        ]
    )

    # Add devices to the bundle
    entries.extend(
        BundleEntry(resource=device, fullUrl=f"Device/{device.id}")
        for device in devices
    )

    # Add document references to the bundle
    entries.extend(
        BundleEntry(
            resource=document_reference,
            fullUrl=f"DocumentReference/{document_reference.id}",
        )
        for document_reference in document_references
    )

    # Remove patient birthDate from the bundle
    entries[0].resource.birthDate = None

    bundle = Bundle(type="collection", entry=entries)

    return bundle


def write_patient_bundle(
    context: GearToolkitContext, subject: flywheel.Subject, bundle: Bundle
):
    """Write a FHIR Bundle resource to a JSON file

    NOTE: The FHIR Bundles should be tested for validity before publishing to a FHIR
          server. See https://fhir.healthit.gov/validator/.
          Other means of validation may be used as well. See https://fhir.healthit.gov
          for more information.

    Args:
        context (GearToolkitContext): The context for the gear job
        subject (flywheel.Subject): The Flywheel Subject that was converted to FHIR.
        bundle (Bundle): The FHIR Bundle resource to write to file
    """
    filename = bundle.entry[0].resource.id + ".fhir.json"
    bundle_json = bundle.json(indent=4)

    # Check to see if the file already exists and if it does, check to see if the
    # contents are the same.
    with TemporaryDirectory() as tmpdir:
        new_bundle_file = Path(tmpdir) / filename
        new_bundle_file.write_text(bundle_json)
        file_ = [fl for fl in subject.files if fl.name == filename]
        if file_:
            file_ = file_[0]
            file_path = Path(tmpdir) / ("existing_" + filename)
            file_.download(file_path)

            existing_bundle_dict = json.load(file_path.open())
            new_bundle_dict = bundle.dict()
            if DeepDiff(existing_bundle_dict, new_bundle_dict):
                # The files are different, so we need to upload the new one
                subject.upload_file(new_bundle_file, signed=False)
        # The file doesn't exist, so we need to upload it
        else:
            subject.upload_file(new_bundle_file, signed=False)

    # Do we want this anymore?
    analysis_path = Path(context.output_dir) / filename
    with open(analysis_path, "w") as outfile:
        outfile.write(bundle_json)

def write_resource_bundles(
    context: GearToolkitContext
):
    """Write a FHIR Bundle resource to a JSON file

    NOTE: The FHIR Bundles should be tested for validity before publishing to a FHIR
          server. See https://fhir.healthit.gov/validator/.
          Other means of validation may be used as well. See https://fhir.healthit.gov
          for more information.

    Args:
        context (GearToolkitContext): The context for the gear job
        bundle_list (List): A list of FHIR Bundle resources to write to file
    """
    output_dict = {}

    # loop through bundles and structure by resource type
    for json_file_path in glob(f'{context.output_dir}/*.json'):
        with open(json_file_path) as json_file:
            bundle_json = json.load(json_file)

            for entry in bundle_json['entry']:
                this_resource = entry['resource']['resourceType']

                if this_resource not in output_dict:
                    output_dict[this_resource] = []

                if entry not in output_dict[this_resource]:
                    output_dict[this_resource].append(entry)

    # clean up existing JSONs
    for json_file_path in glob(f'{context.output_dir}/*.json'):
        os.remove(json_file_path)

    # save resource bundles as JSONs in the analysis container
    for resource_type in output_dict:
        filename = f"{resource_type}.fhir.json"

        analysis_path = Path(context.output_dir) / filename
        with open(analysis_path, "w") as outfile:
            # outfile.write(output_dict[resource_type])
            json.dump(output_dict[resource_type], outfile, indent=4)
