package dbmeter.irdb;

import java.lang.reflect.InvocationHandler;
import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;
import java.lang.reflect.Proxy;
import java.sql.SQLException;
import java.sql.SQLFeatureNotSupportedException;

/**
 * Builds JDBC interface instances from plain classes: a call is forwarded to the public method of the backing
 * object with the same name and parameter types; anything not implemented throws
 * SQLFeatureNotSupportedException. This keeps the adapter to the methods that matter.
 */
final class Jdbc {
    private Jdbc() {
    }

    static <T> T proxy(Class<T> iface, Object impl) {
        InvocationHandler h = (p, m, args) -> invoke(iface, impl, p, m, args);
        return iface.cast(Proxy.newProxyInstance(iface.getClassLoader(), new Class<?>[] {iface}, h));
    }

    private static Object invoke(Class<?> iface, Object impl, Object proxy, Method m, Object[] args) throws Throwable {
        switch (m.getName()) {
            case "unwrap":
                if (((Class<?>) args[0]).isInstance(proxy)) {
                    return proxy;
                }
                throw new SQLException("not a wrapper for " + args[0]);
            case "isWrapperFor":
                return ((Class<?>) args[0]).isInstance(proxy);
            case "hashCode":
                return System.identityHashCode(proxy);
            case "equals":
                return proxy == args[0];
            case "toString":
                return iface.getSimpleName() + "(" + impl + ")";
            default:
                break;
        }
        Method target;
        try {
            target = impl.getClass().getMethod(m.getName(), m.getParameterTypes());
        } catch (NoSuchMethodException e) {
            throw new SQLFeatureNotSupportedException("IRDB adapter: " + iface.getSimpleName() + "." + m.getName()
                    + " is not supported");
        }
        try {
            return target.invoke(impl, args);
        } catch (InvocationTargetException e) {
            throw e.getCause();
        }
    }
}
