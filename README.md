# Nifti to DICOM (project-level) <!-- omit in toc -->

- [Overview](#overview)
  - [Summary](#summary)
  - [License](#license)
  - [Classification](#classification)
- [Usage](#usage)
  - [Requirements](#requirements)
  - [Inputs](#inputs)
  - [Config](#config)
  - [Outputs](#outputs)

## Overview

### Summary

Run nifti to DICOM conversion for all nifti files in a project
(skips niftis that already have a corresponding DICOM).

### License

_License:_ MIT

### Classification

_Category:_ Analysis

_Gear Level:_

- [x] Project
- [ ] Subject
- [ ] Session
- [ ] Acquisition
- [ ] Analysis

## Usage

### Requirements

### Inputs

This gear has no file input. The gear is executed over all
files of a project.

### Config

- _debug_
  - **Type**: _boolean_
  - **Description**: _Log debug messages_
  - **Default**: _False_

### Outputs

None
