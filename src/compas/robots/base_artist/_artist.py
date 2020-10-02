from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import abc
import itertools

from compas.geometry import Frame
from compas.geometry import Scale
from compas.geometry import Transformation

ABC = abc.ABCMeta('ABC', (object,), {'__slots__': ()})


__all__ = [
    'BaseRobotModelArtist'
]


class AbstractRobotModelArtist(ABC):
    @abc.abstractmethod
    def transform(self, geometry, transformation):
        """Transforms a CAD-specific geometry using a **COMPAS** transformation.

        Parameters
        ----------
        geometry : object
            A CAD-specific (i.e. native) geometry object as returned by :meth:`draw_geometry`.
        transformation : `Transformation`
            **COMPAS** transformation to update the geometry object.
        """
        raise NotImplementedError

    @abc.abstractmethod
    def draw_geometry(self, geometry, name=None, color=None):
        """Draw a **COMPAS** geometry in the respective CAD environment.

        Note
        ----
        This is an abstract method that needs to be implemented by derived classes.

        Parameters
        ----------
        geometry : :class:`Mesh`
            Instance of a **COMPAS** mesh
        name : str, optional
            The name of the mesh to draw.

        Returns
        -------
        object
            CAD-specific geometry
        """
        raise NotImplementedError


class BaseRobotModelArtist(AbstractRobotModelArtist):
    """Provides common functionality to most robot model artist implementations.

    In **COMPAS**, the `artists` are classes that assist with the visualization of
    datastructures and models, in a way that maintains the data separated from the
    specific CAD interfaces, while providing a way to leverage native performance
    of the CAD environment.

    There are two methods that implementers of this base class should provide, one
    is concerned with the actual creation of geometry in the native format of the
    CAD environment (:meth:`draw_geometry`) and the other is one to apply a transformation
    to geometry (:meth:`transform`).

    Attributes
    ----------
    model : :class:`compas.robots.RobotModel`
        Instance of a robot model.
    """

    def __init__(self, model):
        super(BaseRobotModelArtist, self).__init__()
        self.model = model
        self.create()
        self.scale_factor = 1.
        self.attached_tool_model = None

    def attach_tool_model(self, tool_model):
        """Attach a tool to the robot artist.

        Parameters
        ----------
        tool_model : :class:`compas.robots.ToolModel`
            The tool that should be attached to the robot's flange.
        """
        self.attached_tool_model = tool_model
        self.create(tool_model.root, 'attached_tool')

        if not tool_model.link_name:
            link = self.model.get_end_effector_link()
            tool_model.link_name = link.name
        else:
            link = self.model.get_link_by_name(tool_model.link_name)

        # originally, there was only this
        ee_frame = link.parent_joint.origin.copy()
        initial_transformation = Transformation.from_frame_to_frame(Frame.worldXY(), ee_frame)
        # but if you attach a tool after applying some update to the robot, then it appears
        # disconnected, being displayed at the original location of the end effector frame.
        # I feel it shouldn't matter the order of calling attach_tool(_model) and update
        # so I suggest the alternative, but maybe it isn't safe.

        if link.collision:
            relative_transformation = link.collision[0].current_transformation
        else:
            relative_transformation = link.visual[0].current_transformation

        transformation = relative_transformation.concatenated(initial_transformation)

        self.update_tool(transformation=transformation)

        tool_model.parent_joint_name = link.parent_joint.name

    def detach_tool_model(self):
        """Detach the tool.
        """
        self.attached_tool_model = None

    def create(self, link=None, name=None):
        """Recursive function that triggers the drawing of the robot model's geometry.

        This method delegates the geometry drawing to the :meth:`draw_geometry`
        method. It transforms the geometry based on the saved initial
        transformation from the robot model.

        Parameters
        ----------
        link : :class:`compas.robots.Link`, optional
            Link instance to create. Defaults to the robot model's root.
        name : :obj:`str`, optional
            Subdomain identifier. # !!! there's got to be a better explanation.

        Returns
        -------
        None
        """
        if link is None:
            link = self.model.root

        for item in itertools.chain(link.visual, link.collision):
            meshes = self.model._get_item_meshes(item)  # !!! this is weird

            if meshes:
                is_visual = hasattr(item, 'get_color')
                color = item.get_color() if is_visual else None

                native_geometry = []
                for i, mesh in enumerate(meshes):
                    # create native geometry
                    mesh_type = 'visual' if is_visual else 'collision'
                    mesh_name_components = [self.model.name, mesh_type, link.name, str(i)]
                    if name:
                        mesh_name_components.insert(2, name)
                    mesh_name = '.'.join(mesh_name_components)
                    native_mesh = self.draw_geometry(mesh, name=mesh_name, color=color)
                    # transform native geometry based on saved init transform
                    self.transform(native_mesh, item.init_transformation)
                    # append to list
                    native_geometry.append(native_mesh)

                item.native_geometry = native_geometry
                item.current_transformation = Transformation()

        for child_joint in link.joints:
            self.create(child_joint.child_link)

    def scale(self, factor):
        """Scales the robot model's geometry by factor (absolute).

        Parameters
        ----------
        factor : float
            The factor to scale the robot with.

        Returns
        -------
        None
        """
        self.model.scale(factor)  # scale the model

        relative_factor = factor / self.scale_factor  # relative scaling factor
        transformation = Scale.from_factors([relative_factor] * 3)
        self.scale_link(self.model.root, transformation)
        self.scale_factor = factor

    def scale_link(self, link, transformation):
        """Recursive function to apply the scale transformation on each link.
        """
        for item in itertools.chain(link.visual, link.collision):
            # Some links have only collision geometry, not visual. These meshes
            # have not been loaded.
            if item.native_geometry:
                for geometry in item.native_geometry:
                    self.transform(geometry, transformation)

        for child_joint in link.joints:
            # Recursive call
            self.scale_link(child_joint.child_link, transformation)

    def _apply_transformation_on_transformed_link(self, item, transformation):
        """Applies a transformation on a link that is already transformed.

        Calculates the relative transformation and applies it to the link
        geometry. This is to prevent the recreation of large meshes.

        Parameters
        ----------
        item: :class:`compas.robots.Visual` or :class:`compas.robots.Collision`
            The visual or collidable object of a link.
        transformation: :class:`Transformation`
            The (absolute) transformation to apply onto the link's geometry.

        Returns
        -------
        None
        """
        relative_transformation = transformation * item.current_transformation.inverse()
        for native_geometry in item.native_geometry:
            self.transform(native_geometry, relative_transformation)
        item.current_transformation = transformation

    def update(self, joint_state, visual=True, collision=True):
        """Triggers the update of the robot geometry.

        Parameters
        ----------
        joint_state : :obj:`dict`
            A dictionary with joint names as keys and joint positions as values.
        visual : bool, optional
            ``True`` if the visual geometry should be also updated, otherwise ``False``.
            Defaults to ``True``.
        collision : bool, optional
            ``True`` if the collision geometry should be also updated, otherwise ``False``.
            Defaults to ``True``.
        """
        transformations = self._update(self.model, joint_state, visual, collision)
        if self.attached_tool_model:
            self.update_tool(visual=visual, collision=collision, transformation=transformations[self.attached_tool_model.parent_joint_name])

    def _update(self, model, joint_state, visual=True, collision=True, parent_transformation=None):
        transformations = model.compute_transformations(joint_state, parent_transformation=parent_transformation)
        for j in model.iter_joints():
            self._transform_link_geometry(j.child_link, transformations[j.name], collision)
        return transformations

    def _transform_link_geometry(self, link, transformation, collision=True):
        for item in link.visual:
            self._apply_transformation_on_transformed_link(item, transformation)
        if collision:
            for item in link.collision:
                # some links have only collision geometry, not visual. These meshes have not been loaded.
                if item.native_geometry:
                    self._apply_transformation_on_transformed_link(item, transformation)

    def update_tool(self, joint_state=None, visual=True, collision=True, transformation=None):
        """Triggers the update of the robot geometry of the tool.

        Parameters
        ----------
        joint_state : :obj:`dict`, optional
            A dictionary with joint names as keys and joint positions as values.
            Defaults to an empty dictionary.
        transformation : :class:`compas.geometry.Transformation`, optional
            The (absolute) transformation to apply to the entire tool's geometry.
            If ``None`` is given, no additional transformation will be applied.
            Defaults to ``None``.
        visual : bool, optional
            ``True`` if the visual geometry should be also updated, otherwise ``False``.
            Defaults to ``True``.
        collision : bool, optional
            ``True`` if the collision geometry should be also updated, otherwise ``False``.
            Defaults to ``True``.
        """
        joint_state = joint_state or {}
        if self.attached_tool_model:
            if transformation is None:
                transformation = self.attached_tool_model.current_transformation
            self._transform_link_geometry(self.attached_tool_model.root, transformation, collision)
            self._update(self.attached_tool_model, joint_state, visual, collision, transformation)
            self.attached_tool_model.current_transformation = transformation

    def draw_visual(self):
        """Draws all visual geometry of the robot model."""
        for native_geometry in self._draw_model_geometry(self.model, 'visual'):
            yield native_geometry
        if self.attached_tool_model:
            for native_geometry in self._draw_model_geometry(self.attached_tool_model, 'visual'):
                yield native_geometry

    def draw_collision(self):
        """Draws all collision geometry of the robot model."""
        for native_geometry in self._draw_model_geometry(self.model, 'collision'):
            yield native_geometry
        if self.attached_tool_model:
            for native_geometry in self._draw_model_geometry(self.attached_tool_model, 'collision'):
                yield native_geometry

    @staticmethod
    def _draw_model_geometry(model, geometry_type):
        for link in model.iter_links():
            for item in getattr(link, geometry_type):
                if item.native_geometry:
                    for native_geometry in item.native_geometry:
                        yield native_geometry