# Copyright 2013 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License").
# You may not use this file except in compliance with the License.
# A copy of the License is located at
#
#  http://aws.amazon.com/apache2.0
#
# or in the "license" file accompanying this file. This file is distributed
# on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either
# express or implied. See the License for the specific language governing
# permissions and limitations under the License.


__all__ = ('WorkflowType', 'ActivityType')

import abc

from .constants import USE_WORKER_TASK_LIST, CHILD_TERMINATE
from .utils import str_or_NONE
from .data_converter import AbstractDataConverter, JSONDataConverter
from .workflow_execution import WorkflowExecution
from .context import (get_context, DecisionContext,
                      StartWorkflowContext, ActivityContext)


class BaseFlowType(object):

    __metaclass__ = abc.ABCMeta

    @abc.abstractmethod
    def to_decision_dict(self):
        raise NotImplementedError()

    @abc.abstractmethod
    def to_registration_options_dict(self):
        raise NotImplementedError()

    @abc.abstractmethod
    def __call__(self):
        raise NotImplementedError()

    @abc.abstractmethod
    def _reset_name(self):
        raise NotImplementedError()


class WorkflowType(BaseFlowType):

    _continue_as_new_keys = ['task_start_to_close_timeout', 'child_policy',
                             'task_list', 'execution_start_to_close_timeout',
                             'version', 'input']

    def __init__(self,
                 version,
                 execution_start_to_close_timeout,
                 task_list=USE_WORKER_TASK_LIST,
                 task_start_to_close_timeout=30,  # as in java flow
                 child_policy=CHILD_TERMINATE,
                 description="",
                 name=None,
                 data_converter=None,
                 skip_registration=False):

        self.version = version
        self.name = name
        self.task_list = task_list
        self.child_policy = child_policy
        self.execution_start_to_close_timeout = execution_start_to_close_timeout
        self.task_start_to_close_timeout = task_start_to_close_timeout
        self.description = description
        self.skip_registration = skip_registration
        self.workflow_id = None
        self.data_converter = data_converter

    @property
    def data_converter(self):
        return self._data_converter

    @data_converter.setter
    def data_converter(self, converter):
        if converter is None:  # set the default
            self._data_converter = JSONDataConverter()
            return

        if isinstance(converter, AbstractDataConverter):
            self._data_converter = converter
            return
        raise TypeError("Converter {0!r} must be a subclass of {1}"
                        .format(converter, AbstractDataConverter.__name__))

    def to_decision_dict(self, input, workflow_id=None, worker_task_list=None, domain=None):
        task_list = self.task_list
        if task_list == USE_WORKER_TASK_LIST:
            task_list = worker_task_list

        serialized_input = self.data_converter.dumps(input)

        decision_dict = {
            'workflow_type': {'version': self.version,
                              'name': self.name},
            'task_list': {'name': str_or_NONE(task_list)},
            'child_policy': str_or_NONE(self.child_policy),
            'execution_start_to_close_timeout': str_or_NONE(
                self.execution_start_to_close_timeout),
            'task_start_to_close_timeout': str_or_NONE(
                self.task_start_to_close_timeout),
            'input': serialized_input}

        # for child workflows
        if workflow_id is not None and self.workflow_id is None:
            decision_dict['workflow_id'] = workflow_id

        if domain is not None:
            decision_dict['domain'] = domain

        # apply any overrides
        context = get_context()

        _decision_dict = {}
        _decision_dict.update(decision_dict)
        _decision_dict.update(context._workflow_options_overrides.items())
        return _decision_dict

    def to_continue_as_new_dict(self, input, worker_task_list):
        decision_dict = self.to_decision_dict(
            input, worker_task_list=worker_task_list)
        continue_as_new_dict = {}
        for key in self._continue_as_new_keys:
            try:
                continue_as_new_dict[key] = decision_dict[key]
            except KeyError:
                pass
        return continue_as_new_dict

    def to_registration_options_dict(self, domain, worker_task_list):
        if self.skip_registration:
            return None

        task_list = self.task_list
        if task_list == USE_WORKER_TASK_LIST:
            task_list = worker_task_list

        registration_options = {
            'domain': domain,
            'version': self.version,
            'name': self.name,
            'default_task_list': {'name': str_or_NONE(task_list)},
            'default_child_policy': str_or_NONE(self.child_policy),
            'default_execution_start_to_close_timeout': str_or_NONE(
                self.execution_start_to_close_timeout),
            'default_task_start_to_close_timeout': str_or_NONE(
                self.task_start_to_close_timeout),
            'description': str_or_NONE(self.description)
        }
        return registration_options

    def _reset_name(self, name, force=False):
        # generate workflow name
        if self.name is None or force:
            self.name = name

    def __call__(self, __class_and_instance, *args, **kwargs):
        _class, _instance = __class_and_instance
        context = get_context()

        if isinstance(context, StartWorkflowContext):
            workflow_id, run_id = context.worker._start_workflow_execution(
                self, *args, **kwargs)
            # create an instance with our new workflow execution info
            workflow_instance = _class(WorkflowExecution(workflow_id, run_id))
            workflow_instance._data_converter = self.data_converter
            return workflow_instance

        elif isinstance(context, DecisionContext):
            if context.decider.execution_started:

                if context.workflow == _instance:
                    continue_as_new_dict = self.to_continue_as_new_dict(
                        [args, kwargs], context.decider.task_list)

                    return context.decider._continue_as_new_workflow_execution(
                        **continue_as_new_dict)
                else:
                    # create an instance with our new workflow execution info
                    # but don't set the workflow_id and run_id as we don't yet
                    # know them
                    workflow_instance = _class(WorkflowExecution(None,
                                                                 None))
                    workflow_instance._data_converter = self.data_converter
                    future = context.decider._handle_start_child_workflow_execution(
                        self, workflow_instance, [args, kwargs])
                    return future
        else:
            raise NotImplementedError("Unsupported context")

    def __hash__(self):
        return hash("{0}{1}".format(self.name, self.version))

    def __repr__(self):
        return "<{} (name={}, version={})>".format(self.__class__.__name__,
                                                   self.name, self.version)


class ActivityType(BaseFlowType):

    def __init__(self,
                 version,
                 name=None,
                 task_list=USE_WORKER_TASK_LIST,
                 heartbeat_timeout=None,
                 schedule_to_start_timeout=None,
                 start_to_close_timeout=None,
                 schedule_to_close_timeout=None,
                 description=None,
                 data_converter=None,
                 skip_registration=False):

        self.version = version
        self.name = name
        self.task_list = task_list
        self.heartbeat_timeout = heartbeat_timeout
        self.schedule_to_start_timeout = schedule_to_start_timeout
        self.start_to_close_timeout = start_to_close_timeout
        self.schedule_to_close_timeout = schedule_to_close_timeout
        self.description = description
        self.skip_registration = skip_registration

        if data_converter is None:
            self.data_converter = JSONDataConverter()
        else:
            self.data_converter = data_converter

    def _set_activities_value(self, key, value):
        if getattr(self, key) is None:
            setattr(self, key, value)

    def _reset_name(self, cls, func, activity_name_prefix):
        # generate activity name
        _name = "%s%s" % (activity_name_prefix, func.__name__)
        if self.name is None:
            _name = "%s.%s" % (cls.__name__, func.__name__)

        else:
            _name = "%s%s" % (activity_name_prefix, self.name)
        self.name = _name

    def to_decision_dict(self):
        decision_dict = {
            'activity_type_version': self.version,
            'activity_type_name': self.name,
            'task_list': {'name':str_or_NONE(self.task_list)},
            'heartbeat_timeout': str_or_NONE(self.heartbeat_timeout),
            'schedule_to_start_timeout': str_or_NONE(
                self.schedule_to_start_timeout),
            'start_to_close_timeout': str_or_NONE(self.start_to_close_timeout),
            'schedule_to_close_timeout': str_or_NONE(
                self.schedule_to_close_timeout),
        }
        return decision_dict

    def to_registration_options_dict(self, domain, worker_task_list):
        if self.skip_registration:
            return None

        task_list = self.task_list
        if task_list == USE_WORKER_TASK_LIST:
            task_list = worker_task_list

        registration_options = {
            'domain': domain,
            'version': self.version,
            'name': self.name,
            'default_task_list': {'name': str_or_NONE(task_list)},
            'default_task_heartbeat_timeout': str_or_NONE(
                self.heartbeat_timeout),
            'default_task_schedule_to_start_timeout': str_or_NONE(
                self.schedule_to_start_timeout),
            'default_task_start_to_close_timeout': str_or_NONE(
                self.start_to_close_timeout),
            'default_task_schedule_to_close_timeout': str_or_NONE(
                self.schedule_to_close_timeout),
            'description': str_or_NONE(self.description)
        }
        return registration_options

    def __call__(self, *args, **kwargs):
        """
        You can call this directly to support dynamic activities.
        """
        context = None
        try:
            context = get_context()
        except AttributeError:  # not in context
            pass

        if not isinstance(context, DecisionContext):
            raise TypeError("ActivityType can only be called in the decision "
                            "context")

        decision_dict = self.to_decision_dict()

        # apply any options overrides
        _decision_dict = {}
        _decision_dict.update(decision_dict)
        _decision_dict.update(context._activity_options_overrides.items())

        return context.decider._handle_execute_activity(
            self, _decision_dict, args, kwargs)


class SignalType(BaseFlowType):

    def __init__(self, name, data_converter=None, workflow_execution=None):
        """
        :param serde: (optional) Serializer to use for serializing inputs
        :type: awsflow.serializers.AbstractSerializer
        """
        self.name = name
        self.data_converter = data_converter
        self.workflow_execution = None

    def to_decision_dict(self):
        raise NotImplementedError("Not applicable to SignalType")

    def to_registration_options_dict(self):
        raise NotImplementedError("Not applicable to SignalType")

    def _reset_name(self):
        raise NotImplementedError("Not applicable to SignalType")

    def __call__(self, *args, **kwargs):
        """
        Records a WorkflowExecutionSignaled event in the workflow execution
        history and creates a decision task for the workflow execution
        identified by the given domain, workflow_execution.
        The event is recorded with the specified user defined name
        and input (if provided).

        :returns: Signals do not return anything
        :rtype: None

        :raises: UnknownResourceFault, OperationNotPermittedFault, RuntimeError
        """
        serialized_input = self.data_converter.dumps([args, kwargs])
        workflow_execution = self.workflow_execution

        context = get_context()
        if not isinstance(context, (StartWorkflowContext, ActivityContext)):
            raise RuntimeError(
                "Unsupported context for this call: %r" % context)

        context.worker._signal_workflow_execution_op(
            domain=context.worker.domain, signal_name=self.name,
            workflow_id=workflow_execution.workflow_id,
            run_id=workflow_execution.run_id,
            input=serialized_input)