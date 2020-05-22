"""
Created on Thu Oct 26 11:06:51 2017

@author: Utku Ozbulak - github.com/utkuozbulak

## Modifications for accomodating other type of CNNs

1. Support for counterfactual

Counterfactual heatmap visualization is supported for negative impact visualization.

2. Separating body and head

Original implementation depends on the last layer to be FC for classification.
This is modified so that separate head can be used.

3. HW flexibility

Added `device` parameter for use with GPU/CPU flexibly.

"""
from PIL import Image
import numpy as np
import torch
from .misc_functions import get_example_params, save_class_activation_images


class CamExtractor():
    """Extracts cam features from the model

    Args:
        model: Body of model.
        target_layer: Index of body layer to extract CAM.
        head: Classifier head of model.
        feed_target: Forward pass will feed (x, target) if True, otherwise feed (x) as usual.
    """
    def __init__(self, model, target_layer, head, feed_target=False):
        self.model = model
        self.target_layer = target_layer
        self.head = head
        self.feed_target = feed_target
        self.gradients = None

    def save_gradient(self, grad):
        self.gradients = grad

    def forward_pass_on_convolutions(self, x):
        """
            Does a forward pass on convolutions, hooks the function at given layer
        """
        conv_output = None
        children = list(self.model.children())
        for module_pos, module in enumerate(children):
            # print(module)
            x = module(x)  # Forward
            # print(x.shape)
            if int(module_pos) == self.target_layer:
                # print('hooked')
                x.register_hook(self.save_gradient)
                conv_output = x  # Save the convolution output on that layer
        return conv_output, x

    def forward_pass(self, x, target):
        """
            Does a full forward pass on the model
        """
        # Forward pass on the convolutions
        conv_output, x = self.forward_pass_on_convolutions(x)
        x = x.view(x.size(0), -1)  # Flatten
        # Forward pass on the classifier
        if self.feed_target:
            x = self.head(x, target)
        else:
            x = self.head(x)
        return conv_output, x


class GradCam():
    """
        Produces class activation map
    """
    def __init__(self, model, target_layer, separate_head=None, device=None, feed_target=None):
        # head is usually separated if it is metric learning head.
        if feed_target is None:
            feed_target = (separate_head is not None)
        # separate head anyway
        if separate_head is None:
            separate_head = list(model.children())[-1]
            model = torch.nn.Sequential(*list(model.children())[:-1])

        self.model = model.eval()
        self.separate_head = separate_head.eval()
        # Define extractor
        self.extractor = CamExtractor(self.model, target_layer,
                                      head=separate_head, feed_target=feed_target)
        self.device = device

    def generate_cam(self, input_image, target_class, counterfactual=False):
        # Full forward pass
        # conv_output is the output of convolutions at specified layer
        # model_output is the final output of the model (1, 1000)
        # print(input_image.shape)
        label = torch.tensor([target_class])
        if self.device is not None:
            input_image = input_image.to(self.device)
            label = label.to(self.device)
        conv_output, model_output = self.extractor.forward_pass(input_image, label)
        # print(conv_output.shape)
        # print(model_output)
        # if target_class is None:
        #     target_class = np.argmax(model_output.data.numpy())
        # Target for backprop
        one_hot_output = torch.FloatTensor(1, model_output.size()[-1]).zero_()
        one_hot_output[0][target_class] = 1
        if self.device is not None:
            one_hot_output = one_hot_output.to(self.device)
        # Zero grads
        # self.model.parameters().zero_grad()
        self.separate_head.zero_grad()
        # Backward pass with specified target
        model_output.backward(gradient=one_hot_output, retain_graph=True)
        # Get hooked gradients
        guided_gradients = self.extractor.gradients.data.cpu().numpy()[0]
        if counterfactual:
            guided_gradients = -guided_gradients
        # Get convolution outputs
        target = conv_output.data.cpu().numpy()[0]
        # Get weights from gradients
        weights = np.mean(guided_gradients, axis=(1, 2))  # Take averages for each gradient
        # Create empty numpy array for cam
        cam = np.ones(target.shape[1:], dtype=np.float32)
        # Multiply each weight with its conv output and then, sum
        for i, w in enumerate(weights):
            cam += w * target[i, :, :]
        cam = np.maximum(cam, 0)
        cam = (cam - np.min(cam)) / (np.max(cam) - np.min(cam))  # Normalize between 0-1
        cam = np.uint8(cam * 255)  # Scale between 0-255 to visualize
        cam = np.uint8(Image.fromarray(cam).resize((input_image.shape[2],
                       input_image.shape[3]), Image.ANTIALIAS))/255
        # ^ I am extremely unhappy with this line. Originally resizing was done in cv2 which
        # supports resizing numpy matrices with antialiasing, however,
        # when I moved the repository to PIL, this option was out of the window.
        # So, in order to use resizing with ANTIALIAS feature of PIL,
        # I briefly convert matrix to PIL image and then back.
        # If there is a more beautiful way, do not hesitate to send a PR.
        return cam


if __name__ == '__main__':
    # Get params
    target_example = 0  # Snake
    (original_image, prep_img, target_class, file_name_to_export, pretrained_model) =\
        get_example_params(target_example)
    # Grad cam
    grad_cam = GradCam(pretrained_model, target_layer=11)
    # Generate cam mask
    cam = grad_cam.generate_cam(prep_img, target_class)
    # Save mask
    save_class_activation_images(original_image, cam, file_name_to_export)
    print('Grad cam completed')
