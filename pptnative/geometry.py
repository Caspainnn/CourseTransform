"""Read-only geometry evidence from actual native PPTX objects."""
from pptx.enum.shapes import MSO_SHAPE_TYPE


def regions(slide, scale, metrics=()):
    text = {o['id']:o for o in metrics}
    result=[]
    def visit(shapes, sx=1, sy=1, tx=0, ty=0, approximate=False):
        for shape in shapes:
            box=[(tx+sx*shape.left)*scale,(ty+sy*shape.top)*scale,
                 sx*shape.width*scale,sy*shape.height*scale]
            rotated=approximate or abs(shape.rotation) > .01
            if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
                transform=shape._element.grpSpPr.xfrm
                cx,cy=transform.chOff.x,transform.chOff.y
                ax=shape.width/transform.chExt.cx if transform.chExt.cx else 1
                ay=shape.height/transform.chExt.cy if transform.chExt.cy else 1
                visit(shape.shapes,sx*ax,sy*ay,tx+sx*(shape.left-ax*cx),ty+sy*(shape.top-ay*cy),rotated)
                continue
            actual=text.get(shape.shape_id)
            item={'id':shape.shape_id,'name':shape.name,'frame_box':[round(v,2) for v in box],
                  'text':shape.text if shape.has_text_frame else '', 'approximate':rotated,
                  'kind':shape.shape_type.name}
            if actual:
                item['text_box']=actual['rendered_bounds']
            if shape.shape_type==MSO_SHAPE_TYPE.PICTURE:
                item['crop']=[shape.crop_left,shape.crop_top,shape.crop_right,shape.crop_bottom]
            result.append(item)
    visit(slide.shapes)
    return result


def intersection(a,b):
    x=max(a[0],b[0]);y=max(a[1],b[1])
    w=min(a[0]+a[2],b[0]+b[2])-x;h=min(a[1]+a[3],b[1]+b[3])-y
    return max(0,w)*max(0,h)


def possible_overlaps(objects):
    pairs=[]
    used=set()
    for a in objects:
        if not a.get('text_box') or a.get('approximate'):
            continue
        box=a['text_box'];area=box[2]*box[3]
        if area<=0:continue
        for b in objects:
            key=tuple(sorted((a['id'],b['id'])))
            if a['id']==b['id'] or key in used or b.get('approximate'):
                continue
            overlap=intersection(box,b.get('text_box',b['frame_box']))
            if 0<overlap/area<.95:
                used.add(key)
                pairs.append({'object_ids':[a['id'],b['id']],'text':a.get('text',''),
                              'other_text':b.get('text',''),'intersection_area':round(overlap,2)})
    return pairs


def validate_visual_geometry(review, objects):
    """Return impossible numeric claims to the vision API, not human adjudication."""
    by_id={o['id']:o for o in objects}
    for issue in review.get('issues',[]):
        if issue.get('severity')!='major':continue
        defect=issue.get('defect_type')
        ids=issue.get('object_ids',[])
        if defect not in ('overlap','crop','contrast','content','other'):
            raise ValueError('Every major visual issue requires defect_type=overlap/crop/contrast/content/other and object_ids from actual_objects')
        if defect=='overlap':
            if len(ids)!=2 or any(i not in by_id for i in ids):
                raise ValueError(f'Major overlap requires exactly two valid actual object_ids. Numerically intersecting candidates: {possible_overlaps(objects)}. Choose the actual visible pair, not an unrelated icon ID.')
            a,b=(by_id[i] for i in ids)
            if not a['approximate'] and not b['approximate'] and intersection(a.get('text_box',a['frame_box']),b.get('text_box',b['frame_box']))==0:
                candidates=[p for p in possible_overlaps(objects) if set(p['object_ids']).intersection(ids)]
                raise ValueError(f"Impossible overlap between actual objects {ids}: their actual bounds are disjoint. a={a}, b={b}. Intersecting candidates for these objects: {candidates}. Reinspect the CURRENT OUTPUT; correct object IDs or remove the unsupported claim.")
        if defect=='crop':
            if not ids or any(i not in by_id for i in ids):
                raise ValueError('Crop claim requires actual object_ids')
            for i in ids:
                obj=by_id[i]
                if 'crop' in obj and not any(obj['crop']):
                    x,y,w,h=obj['frame_box']
                    if x>=0 and y>=0 and x+w<=1282 and y+h<=722:
                        raise ValueError(f"Image {i} preserves the FULL original pixels (all crop=0) inside the canvas. If another object obscures it, report overlap with its ID. Otherwise compare the original asset border and move a source defect to source_limitations; do not claim generated crop.")
