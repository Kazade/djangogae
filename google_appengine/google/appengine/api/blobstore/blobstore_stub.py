#!/usr/bin/env python
#
# Copyright 2007 Google Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#




"""Datastore backed Blobstore API stub.

Class:
  BlobstoreServiceStub: BlobstoreService stub backed by datastore.
"""










import base64
import os
import time
import urlparse

from google.appengine.api import apiproxy_stub
from google.appengine.api import datastore
from google.appengine.api import datastore_errors
from google.appengine.api import datastore_types
from google.appengine.api import users
from google.appengine.api import blobstore
from google.appengine.api.blobstore import blobstore_service_pb
from google.appengine.runtime import apiproxy_errors


__all__ = ['BlobStorage',
           'BlobstoreServiceStub',
           'ConfigurationError',
           'CreateUploadSession',
           'Error',
          ]


class Error(Exception):
  """Base blobstore error type."""


class ConfigurationError(Error):
  """Raised when environment is not correctly configured."""


_UPLOAD_SESSION_KIND = '__BlobUploadSession__'
_GS_INFO_KIND = '__GsFileInfo__'


def CreateUploadSession(creation,
                        success_path,
                        user,
                        max_bytes_per_blob,
                        max_bytes_total):
  """Create upload session in datastore.

  Creates an upload session and puts it in Datastore to be referenced by
  upload handler later.

  Args:
    creation: Creation timestamp.
    success_path: Path in users application to call upon success.
    user: User that initiated this upload, if any.
    max_bytes_per_blob: Maximum number of bytes for any blob in the upload.
    max_bytes_total: Maximum aggregate bytes for all blobs in the upload.

  Returns:
    String encoded key of new Datastore entity.
  """
  entity = datastore.Entity(_UPLOAD_SESSION_KIND, namespace='')
  entity.update({'creation': creation,
                 'success_path': success_path,
                 'user': user,
                 'state': 'init',
                 'max_bytes_per_blob': max_bytes_per_blob,
                 'max_bytes_total': max_bytes_total})
  datastore.Put(entity)
  return str(entity.key())


class BlobStorage(object):
  """Base class for defining how blobs are stored.

  This base class merely defines an interface that all stub blob-storage
  mechanisms must implement.
  """

  def StoreBlob(self, blob_key, blob_stream):
    """Store blob stream.

    Implement this method to persist blob data.

    Args:
      blob_key: Blob key of blob to store.
      blob_stream: Stream or stream-like object that will generate blob content.
    """
    raise NotImplementedError('Storage class must override StoreBlob method.')

  def OpenBlob(self, blob_key):
    """Open blob for streaming.

    Args:
      blob_key: Blob-key of existing blob to open for reading.

    Returns:
      Open file stream for reading blob.  Caller is responsible for closing
      file.
    """
    raise NotImplementedError('Storage class must override OpenBlob method.')

  def DeleteBlob(self, blob_key):
    """Delete blob data from storage.

    Args:
      blob_key: Blob-key of existing blob to delete.
    """
    raise NotImplementedError('Storage class must override DeleteBlob method.')


class BlobstoreServiceStub(apiproxy_stub.APIProxyStub):
  """Datastore backed Blobstore service stub.

  This stub stores manages upload sessions in the Datastore and must be
  provided with a blob_storage object to know where the actual blob
  records can be found after having been uploaded.

  This stub does not handle the actual creation of blobs, neither the BlobInfo
  in the Datastore nor creation of blob data in the blob_storage.  It does,
  however, assume that another part of the system has created these and
  uses these objects for deletion.

  An upload session is created when the CreateUploadURL request is handled and
  put in the Datastore under the __BlobUploadSession__ kind.  There is no
  analog for this kind on a production server. Other than creation, this stub
  not work with session objects.  The URLs created by this service stub are:

    http://<appserver-host>:<appserver-port>/<uploader-path>/<session-info>

  This is very similar to what the URL is on a production server.  The session
  info is the string encoded version of the session entity
  """

  _ACCEPTS_REQUEST_ID = True
  GS_BLOBKEY_PREFIX = 'encoded_gs_file:'

  def __init__(self,
               blob_storage,
               time_function=time.time,
               service_name='blobstore',
               uploader_path='_ah/upload/',
               request_data=None):
    """Constructor.

    Args:
      blob_storage: BlobStorage class instance used for blob storage.
      time_function: Used for dependency injection in tests.
      service_name: Service name expected for all calls.
      uploader_path: Path to upload handler pointed to by URLs generated
        by this service stub.
      request_data: A apiproxy_stub.RequestData instance used to look up state
        associated with the request that generated an API call.
    """
    super(BlobstoreServiceStub, self).__init__(service_name,
                                               request_data=request_data)
    self.__storage = blob_storage
    self.__time_function = time_function
    self.__next_session_id = 1
    self.__uploader_path = uploader_path

  @property
  def storage(self):
    """Access BlobStorage used by service stub.

    Returns:
      BlobStorage instance used by blobstore service stub.
    """
    return self.__storage

  def _GetEnviron(self, name):
    """Helper method ensures environment configured as expected.

    Args:
      name: Name of environment variable to get.

    Returns:
      Environment variable associated with name.

    Raises:
      ConfigurationError if required environment variable is not found.
    """
    try:
      return os.environ[name]
    except KeyError:
      raise ConfigurationError('%s is not set in environment.' % name)

  def _CreateSession(self,
                     success_path,
                     user,
                     max_bytes_per_blob=None,
                     max_bytes_total=None):
    """Create new upload session.

    Args:
      success_path: Application path to call upon successful POST.
      user: User that initiated the upload session.
      max_bytes_per_blob: Maximum number of bytes for any blob in the upload.
      max_bytes_total: Maximum aggregate bytes for all blobs in the upload.

    Returns:
      String encoded key of a new upload session created in the datastore.
    """
    return CreateUploadSession(self.__time_function(),
                               success_path,
                               user,
                               max_bytes_per_blob,
                               max_bytes_total)

  def _Dynamic_CreateUploadURL(self, request, response, request_id):
    """Create upload URL implementation.

    Create a new upload session.  The upload session key is encoded in the
    resulting POST URL.  This URL is embedded in a POST form by the application
    which contacts the uploader when the user posts.

    Args:
      request: A fully initialized CreateUploadURLRequest instance.
      response: A CreateUploadURLResponse instance.
      request_id: A unique string identifying the request associated with the
          API call.
    """
    max_bytes_per_blob = None
    max_bytes_total = None

    if request.has_max_upload_size_per_blob_bytes():
      max_bytes_per_blob = request.max_upload_size_per_blob_bytes()

    if request.has_max_upload_size_bytes():
      max_bytes_total = request.max_upload_size_bytes()

    session = self._CreateSession(request.success_path(),
                                  users.get_current_user(),
                                  max_bytes_per_blob,
                                  max_bytes_total)

    protocol, host, _, _, _, _ = urlparse.urlparse(
        self.request_data.get_request_url(request_id))

    response.set_url('%s://%s/%s%s' % (protocol, host, self.__uploader_path,
                                       session))

  def _Dynamic_DeleteBlob(self, request, response, unused_request_id):
    """Delete a blob by its blob-key.

    Delete a blob from the blobstore using its blob-key.  Deleting blobs that
    do not exist is a no-op.

    Args:
      request: A fully initialized DeleteBlobRequest instance.
      response: Not used but should be a VoidProto.
    """
    for blob_key in request.blob_key_list():
      if blob_key.startswith(self.GS_BLOBKEY_PREFIX):
        key = datastore_types.Key.from_path(_GS_INFO_KIND,
                                            str(blob_key),
                                            namespace='')
      else:
        key = datastore_types.Key.from_path(blobstore.BLOB_INFO_KIND,
                                            str(blob_key),
                                            namespace='')

      datastore.Delete(key)
      self.__storage.DeleteBlob(blob_key)

  def _Dynamic_FetchData(self, request, response, unused_request_id):
    """Fetch a blob fragment from a blob by its blob-key.

    Fetches a blob fragment using its blob-key.  Start index is inclusive,
    end index is inclusive.  Valid requests for information outside of
    the range of the blob return a partial string or empty string if entirely
    out of range.

    Args:
      request: A fully initialized FetchDataRequest instance.
      response: A FetchDataResponse instance.

    Raises:
      ApplicationError when application has the following errors:
        INDEX_OUT_OF_RANGE: Index is negative or end > start.
        BLOB_FETCH_SIZE_TOO_LARGE: Request blob fragment is larger than
          MAX_BLOB_FRAGMENT_SIZE.
        BLOB_NOT_FOUND: If invalid blob-key is provided or is not found.
    """

    start_index = request.start_index()
    if start_index < 0:
      raise apiproxy_errors.ApplicationError(
          blobstore_service_pb.BlobstoreServiceError.DATA_INDEX_OUT_OF_RANGE)


    end_index = request.end_index()
    if end_index < start_index:
      raise apiproxy_errors.ApplicationError(
          blobstore_service_pb.BlobstoreServiceError.DATA_INDEX_OUT_OF_RANGE)


    fetch_size = end_index - start_index + 1
    if fetch_size > blobstore.MAX_BLOB_FETCH_SIZE:
      raise apiproxy_errors.ApplicationError(
          blobstore_service_pb.BlobstoreServiceError.BLOB_FETCH_SIZE_TOO_LARGE)


    blob_key = request.blob_key()
    blob_info_key = datastore.Key.from_path(blobstore.BLOB_INFO_KIND,
                                            blob_key,
                                            namespace='')
    try:
      datastore.Get(blob_info_key)
    except datastore_errors.EntityNotFoundError, err:
      raise apiproxy_errors.ApplicationError(
          blobstore_service_pb.BlobstoreServiceError.BLOB_NOT_FOUND)


    blob_file = self.__storage.OpenBlob(blob_key)
    blob_file.seek(start_index)
    response.set_data(blob_file.read(fetch_size))

  def _Dynamic_DecodeBlobKey(self, request, response, unused_request_id):
    """Decode a given blob key: data is simply base64-decoded.

    Args:
      request: A fully-initialized DecodeBlobKeyRequest instance
      response: A DecodeBlobKeyResponse instance.
    """
    for blob_key in request.blob_key_list():
      response.add_decoded(blob_key.decode('base64'))

  def _Dynamic_CreateEncodedGoogleStorageKey(self, request, response,
                                             unused_request_id):
    """Create an encoded blob key that represents a Google Storage file.

    For now we'll just base64 encode the Google Storage filename, APIs that
    accept encoded blob keys will need to be able to support Google Storage
    files or blobstore files based on decoding this key.

    Args:
      request: A fully-initialized CreateEncodedGoogleStorageKeyRequest
        instance.
      response: A CreateEncodedGoogleStorageKeyResponse instance.
    """
    filename = request.filename()
    response.set_blob_key( self.GS_BLOBKEY_PREFIX +
                          base64.urlsafe_b64encode(filename))

  def CreateBlob(self, blob_key, content):
    """Create new blob and put in storage and Datastore.

    This is useful in testing where you have access to the stub.

    Args:
      blob_key: String blob-key of new blob.
      content: Content of new blob as a string.

    Returns:
      New Datastore entity without blob meta-data fields.
    """
    entity = datastore.Entity(blobstore.BLOB_INFO_KIND,
                              name=blob_key, namespace='')
    entity['size'] = len(content)
    datastore.Put(entity)
    self.storage.CreateBlob(blob_key, content)
    return entity
