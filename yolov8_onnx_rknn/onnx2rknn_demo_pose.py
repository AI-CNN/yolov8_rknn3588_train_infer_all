import os
import urllib
import traceback
import time
import sys
import numpy as np
import cv2
from rknn.api import RKNN
from math import exp

ONNX_MODEL = './yolov8n-pose.onnx'
RKNN_MODEL = './yolov8n-pose.rknn'

# ONNX_MODEL = './yolov8pos_relu_zq.onnx'
# RKNN_MODEL = './yolov8pos_relu_zq.rknn'


DATASET = './dataset.txt'

QUANTIZE_ON = True


color_list = [(0, 0, 255), (0, 255, 0), (255, 0, 0)]
skeleton = [[16, 14], [14, 12], [17, 15], [15, 13], [12, 13], [6, 12], [7, 13], [6, 7], [6, 8], [7, 9], [8, 10], [9, 11],
            [2, 3], [1, 2], [1, 3], [2, 4], [3, 5], [4, 6], [5, 7]]

CLASSES = ['person']

meshgrid = []

class_num = len(CLASSES)
headNum = 3
keypoint_num = 17

strides = [8, 16, 32]
mapSize = [[80, 80], [40, 40], [20, 20]]
nmsThresh = 0.55
objectThresh = 0.5

input_imgH = 640
input_imgW = 640


class DetectBox:
    def __init__(self, classId, score, xmin, ymin, xmax, ymax, pose):
        self.classId = classId
        self.score = score
        self.xmin = xmin
        self.ymin = ymin
        self.xmax = xmax
        self.ymax = ymax
        self.pose = pose


def GenerateMeshgrid():
    for index in range(headNum):
        for i in range(mapSize[index][0]):
            for j in range(mapSize[index][1]):
                meshgrid.append(j + 0.5)
                meshgrid.append(i + 0.5)


def IOU(xmin1, ymin1, xmax1, ymax1, xmin2, ymin2, xmax2, ymax2):
    xmin = max(xmin1, xmin2)
    ymin = max(ymin1, ymin2)
    xmax = min(xmax1, xmax2)
    ymax = min(ymax1, ymax2)

    innerWidth = xmax - xmin
    innerHeight = ymax - ymin

    innerWidth = innerWidth if innerWidth > 0 else 0
    innerHeight = innerHeight if innerHeight > 0 else 0

    innerArea = innerWidth * innerHeight

    area1 = (xmax1 - xmin1) * (ymax1 - ymin1)
    area2 = (xmax2 - xmin2) * (ymax2 - ymin2)

    total = area1 + area2 - innerArea

    return innerArea / total


def NMS(detectResult):
    predBoxs = []

    sort_detectboxs = sorted(detectResult, key=lambda x: x.score, reverse=True)

    for i in range(len(sort_detectboxs)):
        xmin1 = sort_detectboxs[i].xmin
        ymin1 = sort_detectboxs[i].ymin
        xmax1 = sort_detectboxs[i].xmax
        ymax1 = sort_detectboxs[i].ymax
        classId = sort_detectboxs[i].classId

        if sort_detectboxs[i].classId != -1:
            predBoxs.append(sort_detectboxs[i])
            for j in range(i + 1, len(sort_detectboxs), 1):
                if classId == sort_detectboxs[j].classId:
                    xmin2 = sort_detectboxs[j].xmin
                    ymin2 = sort_detectboxs[j].ymin
                    xmax2 = sort_detectboxs[j].xmax
                    ymax2 = sort_detectboxs[j].ymax
                    iou = IOU(xmin1, ymin1, xmax1, ymax1, xmin2, ymin2, xmax2, ymax2)
                    if iou > nmsThresh:
                        sort_detectboxs[j].classId = -1
    return predBoxs


def sigmoid(x):
    return 1 / (1 + exp(-x))


def postprocess(out, img_h, img_w):
    print('postprocess ... ')

    detectResult = []

    output = []
    for i in range(len(out)):
        output.append(out[i].reshape((-1)))

    scale_h = img_h / input_imgH
    scale_w = img_w / input_imgW

    gridIndex = -2

    for index in range(headNum):
        reg = output[index * 2 + 0]
        cls = output[index * 2 + 1]
        pose = output[headNum * 2 + index]

        for h in range(mapSize[index][0]):
            for w in range(mapSize[index][1]):
                gridIndex += 2

                for cl in range(class_num):
                    cls_val = sigmoid(cls[cl * mapSize[index][0] * mapSize[index][1] + h * mapSize[index][1] + w])

                    if cls_val > objectThresh:
                        x1 = (meshgrid[gridIndex + 0] - reg[0 * mapSize[index][0] * mapSize[index][1] + h * mapSize[index][1] + w]) * strides[index]
                        y1 = (meshgrid[gridIndex + 1] - reg[1 * mapSize[index][0] * mapSize[index][1] + h * mapSize[index][1] + w]) * strides[index]
                        x2 = (meshgrid[gridIndex + 0] + reg[2 * mapSize[index][0] * mapSize[index][1] + h * mapSize[index][1] + w]) * strides[index]
                        y2 = (meshgrid[gridIndex + 1] + reg[3 * mapSize[index][0] * mapSize[index][1] + h * mapSize[index][1] + w]) * strides[index]

                        xmin = x1 * scale_w
                        ymin = y1 * scale_h
                        xmax = x2 * scale_w
                        ymax = y2 * scale_h

                        xmin = xmin if xmin > 0 else 0
                        ymin = ymin if ymin > 0 else 0
                        xmax = xmax if xmax < img_w else img_w
                        ymax = ymax if ymax < img_h else img_h

                        poseResult = []
                        for kc in range(keypoint_num):
                            px = pose[(kc * 3 + 0) * mapSize[index][0] * mapSize[index][1] + h * mapSize[index][1] + w]
                            py = pose[(kc * 3 + 1) * mapSize[index][0] * mapSize[index][1] + h * mapSize[index][1] + w]
                            vs = sigmoid(pose[(kc * 3 + 2) * mapSize[index][0] * mapSize[index][1] + h * mapSize[index][1] + w])

                            x = (px * 2.0 + (meshgrid[gridIndex + 0] - 0.5)) * strides[index] * scale_w
                            y = (py * 2.0 + (meshgrid[gridIndex + 1] - 0.5)) * strides[index] * scale_h

                            poseResult.append(vs)
                            poseResult.append(x)
                            poseResult.append(y)
                        # print(poseResult)
                        box = DetectBox(cl, cls_val, xmin, ymin, xmax, ymax, poseResult)
                        detectResult.append(box)

    # NMS
    print('detectResult:', len(detectResult))
    predBox = NMS(detectResult)

    return predBox


def export_rknn_inference(img):
    # Create RKNN object
    rknn = RKNN(verbose=True)

    # pre-process config
    print('--> Config model')
    rknn.config(mean_values=[[0, 0, 0]], std_values=[[255, 255, 255]], quantized_algorithm='normal', quantized_method='channel', target_platform='rk3588')
    print('done')

    # Load ONNX model
    print('--> Loading model')
    ret = rknn.load_onnx(model=ONNX_MODEL, outputs=["cls1", "reg1", "cls2", "reg2", "cls3", "reg3", "ps1", "ps2", "ps3"])
    if ret != 0:
        print('Load model failed!')
        exit(ret)
    print('done')

    # Build model
    print('--> Building model')
    ret = rknn.build(do_quantization=QUANTIZE_ON, dataset=DATASET, rknn_batch_size=1)
    if ret != 0:
        print('Build model failed!')
        exit(ret)
    print('done')

    # Export RKNN model
    print('--> Export rknn model')
    ret = rknn.export_rknn(RKNN_MODEL)
    if ret != 0:
        print('Export rknn model failed!')
        exit(ret)
    print('done')

    # Init runtime environment
    print('--> Init runtime environment')
    ret = rknn.init_runtime()
    # ret = rknn.init_runtime(target='rk3566')
    if ret != 0:
        print('Init runtime environment failed!')
        exit(ret)
    print('done')

    # Inference
    print('--> Running model')
    outputs = rknn.inference(inputs=[img])
    rknn.release()
    print('done')

    return outputs


if __name__ == '__main__':
    print('This is main ...')
    GenerateMeshgrid()
    img_path = './test.jpg'
    orig_img = cv2.imread(img_path)
    img_h, img_w = orig_img.shape[:2]
    
    
    origimg = cv2.resize(orig_img, (input_imgW, input_imgH), interpolation=cv2.INTER_LINEAR)
    origimg = cv2.cvtColor(origimg, cv2.COLOR_BGR2RGB)
    
    img = np.expand_dims(origimg, 0)

    outputs = export_rknn_inference(img)

    out = []
    for i in range(len(outputs)):
        out.append(outputs[i])
    predbox = postprocess(out, img_h, img_w)

    print('obj num is :', len(predbox))

    for i in range(len(predbox)):
        xmin = int(predbox[i].xmin)
        ymin = int(predbox[i].ymin)
        xmax = int(predbox[i].xmax)
        ymax = int(predbox[i].ymax)
        classId = predbox[i].classId
        score = predbox[i].score

        cv2.rectangle(orig_img, (xmin, ymin), (xmax, ymax), (128, 128, 0), 2)
        ptext = (xmin, ymin)
        title = CLASSES[classId] + "%.2f" % score
        cv2.putText(orig_img, title, ptext, cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2, cv2.LINE_AA)

        pose = predbox[i].pose
        for i in range(0, keypoint_num):
            if pose[i * 3] > 0.5:
                if i < 5:
                    color = color_list[0]
                elif 5 <= i < 12:
                    color = color_list[1]
                else:
                    color = color_list[2]
                cv2.circle(orig_img, (int(pose[i * 3 + 1]), int(pose[i * 3 + 2])), 2, color, 5)

        for i, sk in enumerate(skeleton):
            if pose[(sk[0] - 1) * 3] > 0.5:
                pos1 = (int(pose[(sk[0] - 1) * 3 + 1]), int(pose[(sk[0] - 1) * 3 + 2]))
                pos2 = (int(pose[(sk[1] - 1) * 3 + 1]), int(pose[(sk[1] - 1) * 3 + 2]))
                if (sk[0] - 1) < 5:
                    color = color_list[0]
                elif 5 <= sk[0] - 1 < 12:
                    color = color_list[1]
                else:
                    color = color_list[2]
                cv2.line(orig_img, pos1, pos2, color, thickness=2, lineType=cv2.LINE_AA)

    cv2.imwrite('./test_rknn_result.jpg', orig_img)
    # cv2.imshow("test", orig_img)
    # cv2.waitKey(0)

