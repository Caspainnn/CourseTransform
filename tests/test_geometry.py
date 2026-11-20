import unittest
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE
from pptnative.geometry import regions, validate_visual_geometry


class RenderEvidence(unittest.TestCase):
    def test_disjoint_bounds_cannot_support_major_overlap(self):
        objects=[{'id':1,'frame_box':[0,0,50,20],'text_box':[0,0,50,20],'approximate':False},
                 {'id':2,'frame_box':[0,40,50,20],'approximate':False}]
        review={'issues':[{'severity':'major','defect_type':'overlap','object_ids':[1,2]}]}
        with self.assertRaisesRegex(ValueError,'disjoint'):
            validate_visual_geometry(review,objects)
        objects[1]['frame_box'][1]=10
        validate_visual_geometry(review,objects)

    def test_full_uncropped_in_canvas_image_is_not_generated_crop(self):
        with self.assertRaisesRegex(ValueError,'FULL original pixels'):
            validate_visual_geometry({'issues':[{'severity':'major','defect_type':'crop','object_ids':[1]}]},
                                     [{'id':1,'frame_box':[10,10,500,300],'crop':[0,0,0,0]}])

    def test_group_child_geometry_is_transformed_to_slide_coordinates(self):
        deck=Presentation();slide=deck.slides.add_slide(deck.slide_layouts[6])
        group=slide.shapes.add_group_shape()
        child=group.shapes.add_shape(MSO_SHAPE.RECTANGLE,100,200,300,400)
        group.left=1000;group.top=2000;group.width=600;group.height=800
        self.assertEqual(regions(slide,1)[0]['frame_box'],[1000,2000,600,800])
