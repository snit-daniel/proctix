---
tags:
- arcface
- auraface
language:
- en
library_name: insightface
license: apache-2.0
---

# AuraFace

## Model Details

**Model Name:** AuraFace  
**Version:** 1.0  
**Model Type:** Deep Learning Model for Face Recognition  
**Architecture:** Resnet100 with Additive Angular Margin Loss (based on ArcFace)

### Paper Reference

The original ArcFace model and its theoretical foundation are described in the paper [ArcFace: Additive Angular Margin Loss for Deep Face Recognition](https://arxiv.org/abs/1801.07698).

### Overview

AuraFace is a highly discriminative face recognition model designed using the Additive Angular Margin Loss approach. It builds upon the principles introduced in ArcFace and has been trained on commercially and publicly available data sources to enable its usage in commercial setting. AuraFace is tailored for scenarios requiring robust and accurate face recognition capabilities with minimal computational overhead.

## Usage Example

To get a face embedding using AuraFace, it can be used via [InsightFace](https://github.com/deepinsight/insightface/tree/master) as shown in the example:

```python
from huggingface_hub import snapshot_download
from insightface.app import FaceAnalysis
import numpy as np
import cv2

snapshot_download(
    "fal/AuraFace-v1",
    local_dir="models/auraface",
)
face_app = FaceAnalysis(
    name="auraface",
    providers=["CUDAExecutionProvider", "CPUExecutionProvider"],
    root=".",
)

input_image = cv2.imread("test.png")

cv2_image = np.array(input_image.convert("RGB"))

cv2_image = cv2_image[:, :, ::-1]
faces = face_app.get(cv2_image)
embedding = faces[0].normed_embedding
```

## Intended Use

### Primary Use Cases

- **E-commerce and Retail**: Implement secure facial recognition for payment systems or personalized shopping experiences.
- **Digital Content Creation**: Use the IP-Adapter for creating consistent digital avatars or characters in games and interactive media.
- **Mobile Applications**: Integrate face recognition features into apps for enhanced user experiences and security.

### Limitations

1. The efficacy of the model in subject preservation may vary on the basis of etchnicity.
2. The generalization of the models is limited due to limitations of the training data.

## Training Data

AuraFace was trained on a commercial dataset comprising face images from various sources. The dataset tries to include a wide range of demographics, lighting conditions, and image qualities to ensure robust performance across different scenarios, however due to the commercial limitation may not extensively cover all ethnicities.

### Data Preprocessing

- **Normalization:** All images were normalized to a standard size and format.
- **Augmentation:** Techniques such as rotation, flipping, and scaling were used to augment the data and improve the model's generalization capabilities.

## Performance



### Benchmark Results

AuraFace has been tested on multiple face recognition benchmarks:

- LFW: 0.99650
- CFP-FP: 0.95186
- AGEDB: 0.96100
- CALFW: 0.94700
- CPLFW: 0.90933

## Ethical Considerations

### Bias and Fairness

Efforts have been made to ensure that AuraFace performs equitably across different demographic groups. However, users should conduct their own assessments to confirm the model's fairness in their specific application context.

### Privacy

AuraFace should be used in compliance with all relevant privacy laws and guidelines. Users are responsible for ensuring that their use of the model respects individual privacy and data protection regulations.

## Conclusion

AuraFace is a powerful and efficient face recognition model designed for commercial applications. It leverages the advanced Additive Angular Margin Loss technique to provide highly accurate and discriminative features. With a commitment to commercial use and ongoing improvements, AuraFace aims to set a new standard in face recognition technology in the commercial space.