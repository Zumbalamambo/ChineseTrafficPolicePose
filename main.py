from __future__ import division
import matplotlib.pyplot as plt
import scipy.io
import skimage.io
import skimage.transform
import os
import numpy as np
import tensorflow as tf
from random import shuffle
import nets
from PIL import Image

MPI_LABEL_PATH = "./dataset/MPI/mpii_human_pose_v1_u12_1/mpii_human_pose_v1_u12_1.mat"
IMAGE_FOLDER_PATH = "./dataset/MPI/images"
MAX_EPOCH = 100
# resize original image
PH, PW = (376, 656)
BATCH_SIZE = 5

class MPISample:
    pass


class Annorect:
    pass


class Objpos:
    pass


class Joint:
    pass


"""
MPISample
    img_h
    img_w
    annorect_list[]
        Annorect
            Objpos
                x 
                y 
"""
# Load all training labels from file
def load_labels_from_mat():
    err_count = 0
    mat = scipy.io.loadmat(MPI_LABEL_PATH)
    release = mat['RELEASE'][0, 0]
    sample_size = release['img_train'].shape[1]
    mpi_sample_list = []
    # imgidx: image idx
    for imgidx in range(0, sample_size):
        # skip if anno is for testing
        # if release['img_train'][0,imgidx] == 0: # testing
        #     continue
        try:
            # mpi_sample: store mat information in python
            mpi_sample = MPISample()
            # anno_image_mat: all annotations of 1 image
            anno_image_mat = release['annolist'][0, imgidx]
            mpi_sample.name = anno_image_mat['image'][0, 0]['name'][0]
            # img_h, img_w: height and width of original image
            image_path = os.path.join(IMAGE_FOLDER_PATH, mpi_sample.name)
            ori_img = Image.open(image_path)
            mpi_sample.img_w, mpi_sample.img_h = ori_img.size
            # annorect_mat: body annotations of 1 image
            annorect_mat = anno_image_mat['annorect']
            mpi_sample.annorect_list = list()
            # ridx: person idx in 1 image
            if annorect_mat.shape[1] == 0:
                raise ValueError("no person rect annotation")
            # Skip if no person pos label
            for ridx in range(0, annorect_mat.shape[1]):
                annorect_person_mat = annorect_mat[0, ridx]
                annorect = Annorect()
                # .x1, .y1, .x2, .y2: coordinates of the head rectangle
                # annorect.x1 = annorect_person_mat['x1'][0,0]
                # annorect.y1 = annorect_person_mat['y1'][0,0]
                # annorect.x2 = annorect_person_mat['x2'][0,0]
                # annorect.y2 = annorect_person_mat['y2'][0,0]
                # objpos: rough human position in the image
                objpos = Objpos()
                objpos.x = annorect_person_mat['objpos'][0, 0]['x'][0, 0]
                objpos.y = annorect_person_mat['objpos'][0, 0]['y'][0, 0]
                annorect.objpos = objpos
                mpi_sample.annorect_list.append(annorect)
            mpi_sample_list.append(mpi_sample)
        except:
            # A field was not found in annotation
            err_count += 1
            # continue  # skip this image
    print("Invalid samples: " + str(err_count))  # Total skipped images
    return mpi_sample_list


# Fetch samples from shuffled sample list
def samples_generator():
    mpi_sample_list = load_labels_from_mat()
    # Epoch
    for epoch in range(0, MAX_EPOCH):
        print("Current Epoch: " + str(epoch))
        shuffle(mpi_sample_list)
        # Single image
        for mpi_label in mpi_sample_list:
            # Image dir + jpg name
            image_path = os.path.join(IMAGE_FOLDER_PATH, mpi_label.name)
            # Load image from file TODO: keep file reader open?
            image_ori = skimage.io.imread(image_path)
            image = skimage.transform \
                .resize(image_ori, [PH, PW], mode='constant', preserve_range=True) \
                .astype(np.uint8)
            image_b = image / 255.0 - 0.5  # value ranged from -0.5 ~ 0.5
            yield (mpi_label, image_b, image_ori)
    yield None


# Compute gaussian map for 1 center
def gaussian_point(img_height, img_width, c_x, c_y, variance):
    gaussian_map = np.zeros([img_height, img_width])
    for x_p in range(img_width):
        for y_p in range(img_height):
            dist_sq = (x_p - c_x) * (x_p - c_x) + \
                      (y_p - c_y) * (y_p - c_y)
            exponent = dist_sq / 2.0 / variance / variance
            gaussian_map[y_p, x_p] = np.exp(-exponent)
    return gaussian_map


# Convert person location x,y to Gaussian peak
def gaussian_image(img_height, img_width, mpi_sample, scale_h_w):
    heatmap = np.zeros([img_height, img_width], np.float32)
    for annorect in mpi_sample.annorect_list:
        y = annorect.objpos.y * scale_h_w[0]
        x = annorect.objpos.x * scale_h_w[1]
        sub_heatmap = gaussian_point(img_height, img_width, x, y, 3)  # Variance
        heatmap += sub_heatmap
    return heatmap


def main(argv=None):
    # Fetch images for a batch
    batch_images, batch_labels = ([], [])
    debug_batch_img_ori = []

    samples_gen = samples_generator()  # Load samples from disk
    # Construct a batch
    for i in range(0, BATCH_SIZE):
        la_im = next(samples_gen)  # (label, image)
        if la_im is None:  # Reached MAX_EPOCH
            print("Done Training.")
            exit(0)  # End the training
        else:
            batch_labels.append(la_im[0])
            batch_images.append(la_im[1])
            debug_batch_img_ori.append(la_im[2])
    batch_images = np.asarray(batch_images, np.float32)
    # Holder tensor for images and labels
    image_holder = tf.placeholder(tf.float32, shape=[None, PH, PW, 3], name="input_image")
    heatmap_gt_holder = tf.placeholder(tf.float32, shape=[None, PH, PW, 1], name="person_heatmap_gt")
    output_n, output_h, output_w, _ = nets.inference_person(batch_images)[-1].get_shape().as_list()
    # person location Gaussian heatmap
    batch_heatmap_gt = []
    for i in range(0, BATCH_SIZE):
        heatmap_scale_h = output_h / batch_labels[i].img_h
        heatmap_scale_w = output_w / batch_labels[i].img_w

        heatmap = gaussian_image(output_h, output_w, batch_labels[i], [heatmap_scale_h, heatmap_scale_w])

        batch_heatmap_gt.append(heatmap)

        plt.figure(1)
        plt.subplot(211)
        plt.imshow(batch_heatmap_gt[i])
        plt.subplot(212)
        plt.imshow(debug_batch_img_ori[i])
        plt.show()
        print("a")


if __name__ == "__main__":
    tf.app.run()


exit(0)
