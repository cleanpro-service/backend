# TODO: аннотировать типы данных. Везде. Абсолютно.

from django.contrib.auth.tokens import default_token_generator
from django.core import mail
from django.http import HttpRequest
from django.shortcuts import get_object_or_404
from django_filters import rest_framework as filters
from djoser.views import UserViewSet
from rest_framework import permissions, serializers, status, viewsets
from rest_framework.decorators import action, api_view
from rest_framework.response import Response

from cleanpro.app_data import (
    DEFAULT_FROM_EMAIL, EMAIL_CONFIRM_SUBJECT, EMAIL_CONFIRM_TEXT
)
from cleanpro.settings import ADDITIONAL_CS
from service.models import CleaningType, Measure, Order, Rating, Service
from users.models import User
from .filters import FilterService
from .permissions import (
    IsAdminOnly,
    IsAdminOrIsOwner,
    IsAdminOrReadOnly,
    IsOwner,
    IsOwnerOrReadOnly,
)
from .serializers import (
    CreateCleaningTypeSerializer,
    CreateServiceSerializer,
    CommentSerializer,
    CustomUserSerializer,
    DateTimeSerializer,
    EmailConfirmSerializer,
    GetCleaningTypeSerializer,
    GetServiceSerializer,
    MeasureSerializer,
    OrderCancelSerializer,
    OrderGetSerializer,
    OrderPostSerializer,
    OrderStatusSerializer,
    PaySerializer,
    RatingSerializer,
)


def send_mail(subject: str, message: str, to: tuple[str]) -> None:
    """Отправляет электронное сообщение.
    "backend=None" означает, что бекенд будет выбран согласно указанному
    значению в settings.EMAIL_BACKEND."""
    with mail.get_connection(backend=None, fail_silently=False) as conn:
        mail.EmailMessage(
            subject=subject,
            body=message,
            from_email=DEFAULT_FROM_EMAIL,
            to=to,
            connection=conn
        ).send(fail_silently=False)
    return


class MeasureViewSet(viewsets.ModelViewSet):
    """Работа с единицами измерения услуг."""
    queryset = Measure.objects.all()
    serializer_class = MeasureSerializer
    permission_classes = (
        permissions.IsAuthenticated,
        IsAdminOrReadOnly,
    )
    pagination_class = None
    http_method_names = ('get', 'post', 'put')


class CleaningTypeViewSet(viewsets.ModelViewSet):
    """Работа с типами услуг."""
    queryset = CleaningType.objects.all()
    permission_classes = (IsAdminOrReadOnly,)
    pagination_class = None
    http_method_names = ('get', 'post', 'put')

    def get_serializer_class(self):
        if (
            self.request.method == 'GET' and
            not self.request.user.is_staff
        ):
            return GetCleaningTypeSerializer
        else:
            return CreateCleaningTypeSerializer


class ServiceViewSet(viewsets.ModelViewSet):
    """Работа с услугами."""
    queryset = Service.objects.all()
    permission_classes = (IsAdminOrReadOnly,)
    filter_backends = (filters.DjangoFilterBackend,)
    filterset_class = FilterService
    http_method_names = ('get', 'post', 'put')

    def get_queryset(self):
        if not self.request.user.is_staff:
            self.pagination_class = None
            return self.queryset.filter(service_type=ADDITIONAL_CS)
        else:
            return self.queryset

    def get_serializer_class(self):
        if (
            self.request.method == 'GET' and
            not self.request.user.is_staff
        ):
            return GetServiceSerializer
        else:
            return CreateServiceSerializer


class UserViewSet(UserViewSet):
    """Список пользователей."""
    serializer_class = CustomUserSerializer
    http_method_names = ('get', 'post', 'put')

    def create(self, request):
        """Создание пользователей (без вывода данных)."""
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        headers = self.get_success_headers(serializer.data)
        return Response(status=status.HTTP_201_CREATED, headers=headers)

    @action(
        detail=True,
        url_path='orders',
        methods=('get',),
        permission_classes=(
            IsAdminOnly,
        )
    )
    def orders(self, request, id):
        """Список заказов пользователя."""
        queryset = Order.objects.filter(
            user=id
        ).select_related('user', 'cleaning_type', 'address')
        page = self.paginate_queryset(queryset)
        serializer = OrderGetSerializer(
            page,
            many=True,
            context={'request': request}
        )
        return self.get_paginated_response(serializer.data)

    @action(
        detail=False,
        url_path='me',
        methods=('get',),
        permission_classes=(permissions.IsAuthenticated,)
    )
    def me(self, request):
        """Личные данные авторизованного пользователя."""
        instance = request.user
        serializer = CustomUserSerializer(instance)
        return Response(serializer.data)


# TODO: обновить эндпоинт, когда придем с Викой к результату.
@api_view(('POST',))
def confirm_mail(request):
    """Подтвердить электронную почту."""
    serializer = EmailConfirmSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    user = get_object_or_404(
        User,
        email=serializer.validated_data.get('email'),
    )
    user.password = default_token_generator.make_token(user)
    user.save()
    send_mail(
        subject=EMAIL_CONFIRM_SUBJECT,
        message=EMAIL_CONFIRM_TEXT.format(
            username=user.username,
            password=user.password,
        ),
        to=(user.email,),
    )
    return Response(
        data="Email has confirmed! Please check you mailbox",
        status=status.HTTP_200_OK,
    )


class OrderViewSet(viewsets.ModelViewSet):
    """Список заказов."""
    http_method_names = ('get', 'post', 'patch',)
    queryset = Order.objects.select_related('user', 'address',).all()
    # TODO: получается, что сейчас любой пользователь может прочитать
    #       чужие заказы? Это нужно сделать только для администратора.
    #       То же самое для PATCH запроса. DELETE я убрал - нельзя никому!
    #       А вот POST - для пользователя.
    # permission_classes = ()

    def get_permissions(self):
        if not self.request.method == 'POST':
            self.permission_classes = (
                permissions.IsAuthenticated,
                IsAdminOrIsOwner,
            )
        return super().get_permissions()

    def get_queryset(self):
        if not self.request.user.is_staff:
            return self.queryset.filter(user=self.request.user)
        else:
            return self.queryset

    def get_serializer_class(self):
        if self.request.method == 'GET':
            return OrderGetSerializer
        else:
            return OrderPostSerializer

    def __modify_order(
            order_id,
            request: HttpRequest,
            serializer_class: serializers,
            ) -> serializers:  # NoQa
        order = get_object_or_404(Order, id=order_id)
        serializer = serializer_class(order, request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return serializer

    @action(
        detail=True,
        methods=('patch',),
        permission_classes=(IsOwner,),
        url_path='pay',
    )
    def pay(self, request, pk):
        """Оплатить заказ."""
        serializer: serializers = self.__modify_order(
            order_id=pk,
            request=request,
            serializer_class=PaySerializer,
        )
        return Response(serializer.data, status=status.HTTP_200_OK)

    @action(
        detail=True,
        methods=('patch',),
        permission_classes=(IsOwner,),
        url_path='cancel',
    )
    def cancel(self, request, pk):
        """Отменить заказ."""
        serializer: serializers = self.__modify_order(
            order_id=pk,
            request=request,
            serializer_class=OrderCancelSerializer,
        )
        return Response(serializer.data, status=status.HTTP_200_OK)

    @action(
        detail=True,
        methods=('patch',),
        permission_classes=(IsOwner,),
    )
    def comment(self, request, pk):
        """Добавить комментарий к заказу."""
        serializer: serializers = self.__modify_order(
            order_id=pk,
            request=request,
            serializer_class=CommentSerializer,
        )
        return Response(serializer.data, status=status.HTTP_200_OK)

    @action(
        detail=True,
        methods=('patch',),
        permission_classes=(IsOwner,)
    )
    def change_datetime(self, request, pk):
        """Перенести заказ."""
        serializer: serializers = self.__modify_order(
            order_id=pk,
            request=request,
            serializer_class=DateTimeSerializer,
        )
        return Response(serializer.data, status=status.HTTP_200_OK)

    @action(
        detail=True,
        methods=('patch',),
        permission_classes=(permissions.IsAdminUser,)
    )
    def change_status(self, request, pk):
        """Изменить статус заказа."""
        serializer: serializers = self.__modify_order(
            order_id=pk,
            request=request,
            serializer_class=OrderStatusSerializer,
        )
        return Response(serializer.data, status=status.HTTP_200_OK)


class RatingViewSet(viewsets.ModelViewSet):
    """Список отзывов."""
    queryset = Rating.objects.all()
    permission_classes = (IsOwnerOrReadOnly,)
    serializer_class = RatingSerializer
    http_method_names = ('get', 'post', 'patch', 'delete')

    def perform_create(self, serializer):
        order_id = self.kwargs.get('order_id')
        order = get_object_or_404(Order, id=order_id)
        serializer.save(user=self.request.user, order=order)
