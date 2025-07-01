"""Main module."""

import logging

log = logging.getLogger(__name__)


def run(context, debug):
    """Run the algorithm defined in this gear.

    Args:
        context (GearContext): The Context object
        debug (bool): The debug flag

    Raises:
        ValueError: Raised if the parent container type is not supported

    Returns:
        int: The Exit Code
    """
    try:
        # This gear is designed to be run on a project or subject container
        destination_container = context.client.get(context.destination["id"])
        # get parent type
        dest_parent_type = destination_container.parent["type"]
        dest_parent_id = destination_container.parent["id"]
        dest_parent = context.client.get(dest_parent_id)

        if dest_parent_type == "project":
            fw = context.client
            project = dest_parent

            # get a DF of all files in the project
            view = fw.View(
                container="acquisition",
                filename="*",
                match="all",
                columns=[
                    "file.name",
                    "file.file_id",
                    "file.type",
                ],
                include_ids=True,
                include_labels=True,
                process_files=False,
                sort=False,
            )

            print('Getting project DataView...')
            project_df = fw.read_view_dataframe(view, project.id)

            if 'dicom' in project_df['file.type'].drop_duplicates().tolist(): # if there are some DICOMs already
                # remove nifti files that already have DICOM versions
                dicom_files = project_df[project_df['file.type'] == 'dicom']
                dicom_files['source_fn'] = dicom_files['file.name'].str.split('_out.dcm', expand=True)[0]+'.nii.gz'
                dicom_files = dicom_files[['acquisition.id','source_fn']]
                nifti_files = project_df[project_df['file.type'] == 'nifti']
                temp_df = nifti_files.merge(dicom_files, how='outer', left_on=['acquisition.id','file.name'], right_on=['acquisition.id','source_fn'], indicator=True)
                nifti_files_to_proc = temp_df[temp_df['_merge'] == 'left_only']
            else:
                nifti_files_to_proc = project_df[project_df['file.type'] == 'nifti']

            nifti_files_to_proc = nifti_files_to_proc.reset_index(drop=True)
            
            # run the conversion gear for each remaining nifti file
            gear_name = 'nifti-to-dicom'
            gear2run = fw.lookup(f'gears/{gear_name}')

            n_files = len(nifti_files_to_proc)
            for ind,row in nifti_files_to_proc.iterrows():
                file_id = row['file.file_id']
                file = fw.get_file(f'{file_id}')
                inputs = {'nifti-file':file}
                job_id = gear2run.run(inputs=inputs, priority='high')
                # job_id = gear2run.run(inputs=inputs, priority='low')
                print(f'Queued file: {ind}/{n_files}')

        # if dest_parent_type == "project":
        #     for subject in dest_parent.subjects():
        #         bundle = get_patient_bundle(
        #             context.client, subject, include_encounter, include_bodystructure
        #         )
        #         write_patient_bundle(context, subject, bundle)
        # elif dest_parent_type == "subject":
        #     bundle = get_patient_bundle(
        #         context.client, dest_parent, include_encounter, include_bodystructure
        #     )
        #     write_patient_bundle(context, dest_parent, bundle)
        
        # if not output_subject_bundle:
        #     write_resource_bundles(context)

        # TODO: add support for session containers
        #       This would be done by passing the session object along with the subject
        #       e.g. get_patient_bundle(context.client, dest_parent.subject, dest_parent)
        else:
            log.debug(
                "Parent container type %s is not supported. Exiting.", dest_parent_type
            )
            raise ValueError(
                f"Parent container type {dest_parent_type} is not supported. Exiting."
            )
    except (ValueError, Exception) as exc:
        log.exception(exc)
        return 1

    return 0
