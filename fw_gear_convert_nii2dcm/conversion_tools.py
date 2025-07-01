
import os
import flywheel
import pandas as pd

target_group = 'dayone'
target_project = 'DayOne_imaging'


def convert_niftis(patient: Patient, session: flywheel.Session) -> Encounter:
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

